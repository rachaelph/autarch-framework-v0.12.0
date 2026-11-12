"""Verify that the ADF update reuses existing assets and overrides one execution."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from autarch.capex.demo_v2_intake import SOURCE_FILES

ROOT = Path(__file__).resolve().parents[1]
AZ = shutil.which("az")
PWSH = shutil.which("pwsh")
BASH = shutil.which("bash")
if BASH is None and Path(r"C:\Program Files\Git\bin\bash.exe").is_file():
    BASH = r"C:\Program Files\Git\bin\bash.exe"


@pytest.fixture(params=["ps1", "sh"])
def conformance_script(request):
    return request.param


def run_conformance(
    tmp_path: Path, service: dict[str, object], source: str, extension: str,
) -> subprocess.CompletedProcess[str]:
    script = ROOT / ".github" / "agents" / "azure-deploy" / "scaffold" / "scripts" / f"scaffold-conformance.{extension}"
    if extension == "ps1":
        if PWSH is None:
            pytest.skip("PowerShell is unavailable")
        command = [PWSH, "-NoProfile", "-File", str(script)]
    else:
        if BASH is None or shutil.which("jq") is None:
            pytest.skip("Bash and jq are required for plan-dependent conformance checks")
        command = [BASH, str(script)]
    session = tmp_path / "session"
    infra = tmp_path / "infra"
    session.mkdir()
    infra.mkdir()
    (session / "prepare-plan.json").write_text(json.dumps({"services": [service]}), encoding="utf-8")
    (infra / "main.bicep").write_text(source, encoding="utf-8")
    command += (
        ["-SessionPath", str(session), "-InfraPath", str(infra)]
        if extension == "ps1" else [str(session), str(infra)]
    )
    return subprocess.run(
        command,
        text=True, capture_output=True, timeout=30,
    )


@pytest.mark.parametrize(
    ("reuse", "resource_name", "expected_failure"),
    [
        (True, "existing-registry", None),
        (False, "existing-registry", "SERVICES-COMPLETE"),
        (None, "existing-registry", "SERVICES-COMPLETE"),
        ("true", "existing-registry", "PLAN-REUSE-TYPE"),
        (True, " ", "PLAN-REUSE-NAME"),
        (True, None, "PLAN-REUSE-NAME"),
    ],
)
def test_conformance_distinguishes_named_reuse_from_provisioning(
    tmp_path, conformance_script, reuse, resource_name, expected_failure,
):
    service = {"name": "Azure Container Registry", "resourceName": resource_name}
    if reuse is not None:
        service["reuseExisting"] = reuse
    result = run_conformance(tmp_path, service, "targetScope = 'resourceGroup'\n", conformance_script)
    assert result.returncode in (0, 1), result.stderr
    report = json.loads(result.stdout)
    if expected_failure is None:
        assert result.returncode == 0 and report["passed"] is True, report
    else:
        assert result.returncode == 1 and report["passed"] is False, report
        assert expected_failure in {item["id"] for item in report["failures"]}


@pytest.mark.parametrize(
    ("resource_type", "passes"),
    [("Microsoft.App/jobs", True), ("Microsoft.App/containerApps", False)],
)
def test_conformance_does_not_confuse_jobs_with_container_apps(
    tmp_path, conformance_script, resource_type, passes,
):
    result = run_conformance(
        tmp_path, {"name": "Azure Container Apps Job"},
        f"resource worker '{resource_type}@2025-01-01' = {{ name: 'worker' }}\n",
        conformance_script,
    )
    assert result.returncode in (0, 1), result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is passes, report
    assert result.returncode == (0 if passes else 1)


def test_reused_dependencies_do_not_disable_iac_security_checks(tmp_path, conformance_script):
    result = run_conformance(
        tmp_path,
        {"name": "Azure Container Registry", "resourceName": "existing-registry", "reuseExisting": True},
        "resource plan 'Microsoft.Web/serverfarms@2023-12-01' = { sku: { name: 'F1' } }\n",
        conformance_script,
    )
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is False
    assert "NO-FREE-SKU" in {item["id"] for item in report["failures"]}


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    if AZ is None:
        pytest.skip("Azure CLI/Bicep is unavailable")
    destination = tmp_path_factory.mktemp("existing-adf") / "template.json"
    result = subprocess.run(
        [AZ, "bicep", "build", "--file", str(ROOT / "infra" / "data-factory" / "capex-existing-demo.bicep"),
         "--outfile", str(destination)],
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(destination.read_bytes())


def activities(template):
    pipeline = next(
        item["properties"] for item in template["resources"] if item["type"].endswith("/pipelines")
    )
    return {item["name"]: item for item in pipeline["activities"]}


def test_only_approved_adf_assets_are_declared_with_opt_in_credential(template):
    assert {item["type"] for item in template["resources"]} == {
        "Microsoft.DataFactory/factories/pipelines", "Microsoft.DataFactory/factories/datasets",
        "Microsoft.DataFactory/factories/credentials",
    }
    assert len(template["resources"]) == 3
    credential = next(item for item in template["resources"] if item["type"].endswith("/credentials"))
    assert credential["condition"] == "[not(empty(parameters('armCredentialName')))]"
    assert credential["properties"]["type"] == "ManagedIdentity"
    assert "cict-ai-cus-capex-id" in credential["properties"]["typeProperties"]["resourceId"]
    assert template["parameters"]["armCredentialName"]["defaultValue"] == ""
    assert template["parameters"]["armCredentialName"]["allowedValues"] == ["", "cred_capex_uami"]
    names = json.dumps([item["name"] for item in template["resources"]])
    assert "pl_capex_intake_check" in names
    assert "ds_capex_landing_files" in names
    assert "capex-demo-v2-sql" not in json.dumps(template)
    assert "capex-task-determination-proof" in json.dumps(template["variables"]["jobId"])
    pipeline = next(item for item in template["resources"] if item["type"].endswith("/pipelines"))
    assert pipeline["properties"]["concurrency"] == 1
    assert pipeline["properties"]["parameters"]["processInvoices"]["defaultValue"] is False
    stages = activities(template)
    guard = stages["RequireProcessingOptIn"]
    assert guard["typeProperties"]["expression"]["value"] == "@pipeline().parameters.processInvoices"
    assert guard["typeProperties"]["ifFalseActivities"][0]["typeProperties"]["errorCode"] == "CAPEX_PROCESSING_NOT_ENABLED"
    assert stages["SelectDemoFiles"]["dependsOn"][0]["activity"] == "RequireProcessingOptIn"


def test_binary_dataset_preserves_input_defaults_and_reuses_linked_service(template):
    dataset = next(
        item["properties"] for item in template["resources"] if item["type"].endswith("/datasets")
    )
    assert dataset["type"] == "Binary"
    assert dataset["linkedServiceName"]["referenceName"] == "ls_capex_landing_blob"
    assert dataset["parameters"]["folderPath"]["defaultValue"] == "capex/input"
    assert dataset["parameters"]["fileName"]["defaultValue"] == ""
    assert dataset["typeProperties"]["location"]["container"] == "landing"
    assert dataset["typeProperties"]["location"]["folderPath"]["value"] == "@dataset().folderPath"
    expression = template["variables"]["fileFilter"]
    assert "equals(item().type, 'File')" in expression
    assert set(re.findall(r"'([^']+\.(?:zip|xlsx))'", expression)) == set(SOURCE_FILES)
    assert activities(template)["RequireAllSources"]["typeProperties"]["ifFalseActivities"][0]["type"] == "Fail"


def test_identity_attachment_preserves_factory_configuration_without_rbac(tmp_path):
    if AZ is None:
        pytest.skip("Azure CLI/Bicep is unavailable")
    destination = tmp_path / "identity.json"
    result = subprocess.run(
        [AZ, "bicep", "build", "--file", str(ROOT / "infra" / "data-factory" / "capex-adf-identity.bicep"),
         "--outfile", str(destination)],
        text=True, capture_output=True, timeout=90,
    )
    assert result.returncode == 0, result.stderr
    compiled = json.loads(destination.read_bytes())
    assert len(compiled["resources"]) == 1
    factory = compiled["resources"][0]
    assert factory["type"] == "Microsoft.DataFactory/factories"
    assert factory["name"] == "cict-ai-cus-capex-adf"
    assert factory["properties"] == "[parameters('factoryProperties')]"
    assert factory["identity"] == "[parameters('factoryIdentity')]"
    assert factory["tags"] == "[parameters('factoryTags')]"
    assert factory["location"] == "[parameters('factoryLocation')]"


def test_override_has_pinned_worker_image_references_and_sql_extraction_opt_ins(template):
    assert template["parameters"]["imageSha256"]["minLength"] == 64
    assert template["parameters"]["imageSha256"]["maxLength"] == 64
    override = template["variables"]["executionTemplate"]
    assert len(override["containers"]) == 1
    container = override["containers"][0]
    assert container["name"] == "capex-task-determination-proof"
    assert "capex/demo-v2-adf@sha256:" in container["image"]
    assert container["command"] == ["python", "-m", "autarch.capex.demo_v2_adf_job"]
    assert "--workflow-run-id" in container["args"] and "__ADF_WORKFLOW_RUN_ID__" in container["args"]
    assert "parameters('governedSqlReferences')" in container["args"]
    assert "--governed-sql-references" in container["args"]
    assert template["parameters"]["governedSqlReferences"]["defaultValue"] is False
    assert "--execute" in container["args"]
    assert {item["name"] for item in container["env"]} == {
        "AZURE_CLIENT_ID", "AZURE_OPENAI_ENDPOINT", "CAPEX_SQL_SERVER", "CAPEX_SQL_DATABASE",
    }
    assert "--sql-persist" in container["args"]
    assert "--fresh-pdf-extraction" in container["args"]
    assert template["parameters"]["pdfRecommendationMode"]["defaultValue"] == "strict"
    assert template["parameters"]["pdfRecommendationMode"]["allowedValues"] == ["strict", "provisional-demo"]
    assert re.search(r"'--pdf-recommendation-mode',\s*parameters\('pdfRecommendationMode'\)", container["args"])
    assert container["resources"] == {"cpu": "[json('1.0')]", "memory": "2Gi"}
    for parameter, name in (
        ("taskMasterSha256", "task-master-us-v2.json"), ("policySha256", "policy-config-v2.json"),
    ):
        actual = ROOT / "examples" / "data" / "capex" / "reference-v2" / name
        assert template["parameters"][parameter]["defaultValue"] == hashlib.sha256(actual.read_bytes()).hexdigest()


def test_start_is_explicit_non_retried_and_not_a_job_update(template):
    stages = activities(template)
    start = stages["StartJob"]
    assert start["policy"]["retry"] == 0
    assert start["dependsOn"][0]["activity"] == "RequireSafeJobConfiguration"
    assert start["typeProperties"]["method"] == "POST"
    assert "/start?api-version=2025-01-01" in start["typeProperties"]["url"]
    assert start["typeProperties"]["turnOffAsync"] is False
    assert start["typeProperties"]["authentication"] == "[variables('authentication')]"
    auth = template["variables"]["authentication"]
    assert "'MSI'" in auth
    assert "'UserAssignedManagedIdentity'" in auth
    assert "'CredentialReference'" in auth
    assert "parameters('armCredentialName')" in auth
    assert "pipeline().RunId" in template["variables"]["startExpression"]
    safe = stages["RequireSafeJobConfiguration"]["typeProperties"]
    assert "replicaRetryLimit, 0" in safe["expression"]["value"]
    assert "parallelism, 1" in safe["expression"]["value"]
    assert safe["ifFalseActivities"][0]["type"] == "Fail"
    assert stages["RequireExecutionIdentity"]["typeProperties"]["ifFalseActivities"][0]["type"] == "Fail"


def test_only_the_named_execution_is_polled_and_failed_reads_terminate(template):
    stages = activities(template)
    until = stages["WaitForExecution"]["typeProperties"]
    assert until["timeout"] == "0.00:25:00"
    nested = {item["name"]: item for item in until["activities"]}
    url = nested["ReadExecution"]["typeProperties"]["url"]["value"].replace("''", "'")
    assert "activity('StartJob').output.name" in url
    assert "/executions/" in url and "latest" not in url.lower()
    assert nested["RecordPollFailure"]["dependsOn"][0]["dependencyConditions"] == ["Failed"]
    assert nested["RecordPollFailure"]["typeProperties"]["value"] == "POLL_FAILED"
    assert "POLL_FAILED" in until["expression"]["value"]
    assert stages["RequireExecutionSucceeded"]["typeProperties"]["ifFalseActivities"][0]["type"] == "Fail"


def test_completion_requires_success_and_run_scoped_marker_not_input_manifest(template):
    stages = activities(template)
    marker = stages["CheckCompletionMarker"]
    assert marker["dependsOn"][0]["activity"] == "RequireExecutionSucceeded"
    assert marker["type"] == "GetMetadata"
    assert marker["typeProperties"]["fieldList"] == ["exists"]
    parameters = marker["typeProperties"]["dataset"]["parameters"]
    assert parameters["fileName"] == "batch.json"
    assert parameters["folderPath"]["value"] == "@concat('capex/output/adf-runs/', pipeline().RunId)"
    assert stages["RequireCompletionMarker"]["typeProperties"]["expression"]["value"] == (
        "@equals(activity('CheckCompletionMarker').output.exists, true)"
    )
    assert stages["RequireCompletionMarker"]["typeProperties"]["ifFalseActivities"][0]["type"] == "Fail"


def test_no_unsupported_nested_if_or_switch_activities(template):
    for item in activities(template).values():
        if item["type"] == "IfCondition":
            for branch in ("ifTrueActivities", "ifFalseActivities"):
                assert not any(
                    child["type"] in {"IfCondition", "Switch", "Until", "ForEach"}
                    for child in item["typeProperties"].get(branch, [])
                )


def test_adf_verification_query_is_read_only_run_scoped_and_uses_actual_counts():
    sql = (ROOT / "sql" / "validation" / "validate_capex_adf_demo_run.sql").read_text()
    executable = re.sub(r"--[^\n]*", "", sql)
    executable = re.sub(r"\bINSERT\s+(?:INTO\s+)?@Suggestions\b", "", executable, flags=re.I)
    assert not re.search(r"\b(?:INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|GRANT|REVOKE)\b", executable, re.I)
    assert "REPLACE_WITH_ADF_RUN_ID" in executable
    assert "SUM(line_count)" in executable
    assert "'$.partitions[0].result.packages'" in executable
    assert "NOT_CALLED_REFERENCE_REVIEW" in executable
    assert "HASHBYTES('SHA2_256', content)" in executable
    assert "ADF_SQL_REVIEW_VERIFIED" in executable
