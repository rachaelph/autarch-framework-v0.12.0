"""Run the new build helper with mocked Azure, never an actual cloud build."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable")

HARNESS = r"""
param($BuildScript, $SourceRoot, $OutputDirectory, $Scenario, $Trace)
$ErrorActionPreference = 'Stop'
$global:Scenario = $Scenario
$global:Trace = $Trace
$global:Tag = ''
function global:Invoke-WebRequest {
    param($Method, $Uri, $InFile, $Headers, $ContentType, $OutFile, [switch]$PassThru, [switch]$SkipHttpErrorCheck)
    if ($Method -eq 'Get') {
        'Mock ADF CLI help validated' | Set-Content -LiteralPath $OutFile
        return @{StatusCode = $(if ($global:Scenario -eq 'logs-failure') {403} else {200})}
    }
    @(tar -tf $InFile | Where-Object { -not $_.EndsWith('/') }) |
        ConvertTo-Json | Set-Content -LiteralPath ($global:Trace + '.context.json')
    return @{StatusCode = $(if ($global:Scenario -eq 'upload-failure') {403} else {201})}
}
function global:az {
    $a = @($args)
    ConvertTo-Json -InputObject $a -Compress | Add-Content -LiteralPath $global:Trace
    $global:LASTEXITCODE = 0
    $command = $a[0..1] -join ' '
    if ($command -eq 'account show') {
        return (@{id = $(if ($global:Scenario -eq 'wrong-target') {'wrong'} else {'0451df15-250e-42f5-b15c-f19a9bb9c21a'});
            tenantId = '19da1f3c-d958-44ea-850d-195c1502525c'} | ConvertTo-Json)
    }
    if ($command -eq 'acr show') {
        return (@{publicNetworkAccess = $(if ($global:Scenario -eq 'network-blocked') {'Disabled'} else {'Enabled'});
            networkRuleSet = @{defaultAction = 'Allow'}} | ConvertTo-Json)
    }
    if ($a[0] -eq 'rest') {
        $url = $a[[array]::IndexOf($a, '--url') + 1]
        if ($url.Contains('/listBuildSourceUploadUrl?')) {
            return '{"uploadUrl":"https://example.invalid/mock-upload","relativePath":"mock.tar.gz"}'
        }
        if ($url.Contains('/listLogSasUrl?')) {
            return '{"logLink":"https://example.invalid/mock-log"}'
        }
        if ($url.Contains('/scheduleRun?')) {
            if ($global:Scenario -eq 'schedule-failure') {$global:LASTEXITCODE = 1; return}
            $path = $a[[array]::IndexOf($a, '--body') + 1].Substring(1)
            $request = Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
            if ($request.dockerFilePath -ne 'Dockerfile.capex-adf' -or
                $request.imageNames.Count -ne 1 -or
                -not $request.imageNames[0].StartsWith('capex/demo-v2-adf:') -or
                @($request.PSObject.Properties | Where-Object Name -eq agentPoolName).Count -ne 0) {
                throw 'Wrong build target or unexpected infrastructure change.'
            }
            $global:Tag = ($request.imageNames[0] -split ':')[1]
            return '{"properties":{"runId":"mock-adf-own-run"}}'
        }
    }
    if ($command -eq 'acr task' -and $a[2] -eq 'show-run') {
        return (@{status = $(if ($global:Scenario -eq 'build-failure') {'Failed'} else {'Succeeded'});
            outputImages = @(@{registry='cictaicusacr.azurecr.io';repository='capex/demo-v2-adf';
                tag=$global:Tag;digest=$(if ($global:Scenario -eq 'bad-digest') {'latest'} else {'sha256:' + ('a' * 64)})})} |
            ConvertTo-Json -Depth 5)
    }
    throw "Unexpected Azure write or command: $($a -join ' ')"
}
try {
    & $BuildScript -SourceRoot $SourceRoot -OutputDirectory $OutputDirectory -AgentPool shared
}
catch {[Console]::Error.WriteLine($_.Exception.Message); exit 1}
"""


def run_build(tmp_path, scenario):
    source = tmp_path / "source"
    files = {
        "Dockerfile.capex-adf": (ROOT / "Dockerfile.capex-adf").read_text(),
        "README.md": "Mock", "pyproject.toml": "[project]\nname='mock'",
        "autarch/__init__.py": "",
        "autarch/capex/schemas/invoice-package-v1.schema.json": "{}",
        "autarch/capex/schemas/invoice-ready-v1.schema.json": "{}",
        "examples/data/capex/reference-v2/task-master-us-v2.json": "{}",
        "examples/data/capex/reference-v2/policy-config-v2.json": "{}",
        "examples/data/capex/reference-v2/asset-class-master-eu-v1.json": "{}",
        "autarch/.env": "DO_NOT_UPLOAD", "autarch/.venv/secret.py": "DO_NOT_UPLOAD",
        "examples/data/capex/invoice.xlsx": "DO_NOT_UPLOAD",
        ".copilot-azure/context.json": "DO_NOT_UPLOAD", "credentials.json": "DO_NOT_UPLOAD",
        "Dockerfile.capex-v2": "DO_NOT_UPLOAD",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS)
    trace = tmp_path / "trace.jsonl"
    evidence = tmp_path / "evidence"
    result = subprocess.run([
        str(PWSH), "-NoProfile", "-File", str(harness),
        "-BuildScript", str(ROOT / "tools" / "Build-Push-CapexAdfAcr.ps1"),
        "-SourceRoot", str(source), "-OutputDirectory", str(evidence),
        "-Scenario", scenario, "-Trace", str(trace),
    ], capture_output=True, text=True, timeout=30)
    calls = [json.loads(line) for line in trace.read_text().splitlines()]
    return result, calls, evidence, trace


def test_separate_build_excludes_sources_secrets_sessions_and_friday(tmp_path):
    result, calls, evidence, trace = run_build(tmp_path, "success")
    assert result.returncode == 0, result.stderr
    uploaded = set(json.loads(Path(str(trace) + ".context.json").read_text()))
    assert {item.removeprefix("./").replace("\\", "/") for item in uploaded} == {
        "Dockerfile.capex-adf", "pyproject.toml", "README.md", "autarch/__init__.py",
        "autarch/capex/schemas/invoice-package-v1.schema.json",
        "autarch/capex/schemas/invoice-ready-v1.schema.json",
        "examples/data/capex/reference-v2/task-master-us-v2.json",
        "examples/data/capex/reference-v2/policy-config-v2.json",
        "examples/data/capex/reference-v2/asset-class-master-eu-v1.json",
    }
    image = json.loads(next(evidence.glob("*/image-reference.json")).read_text())
    assert image["image"] == "cictaicusacr.azurecr.io/capex/demo-v2-adf@sha256:" + "a" * 64
    assert image["imageSha256"] == "a" * 64
    assert image["adfCliHelpValidatedDuringBuild"]
    assert not any("update" in call or "role" in call or "deployment" in call for call in calls)


@pytest.mark.parametrize("scenario", [
    "wrong-target", "network-blocked", "upload-failure", "schedule-failure",
    "build-failure", "logs-failure", "bad-digest",
])
def test_build_failures_never_publish_an_image_reference(tmp_path, scenario):
    result, calls, evidence, _ = run_build(tmp_path, scenario)
    assert result.returncode != 0
    assert not list(evidence.glob("*/image-reference.json"))
    assert not any("update" in call or "role" in call or "deployment" in call for call in calls)
    assert "mock-upload" not in result.stderr
    assert "mock-log" not in result.stderr


def test_worker_image_requires_sql_driver_pdf_renderer_and_both_cli_checks():
    dockerfile = (ROOT / "Dockerfile.capex-adf").read_text()
    assert '".[capex,capex-demo]"' in dockerfile
    assert "ACCEPT_EULA=Y" in dockerfile and "msodbcsql18" in dockerfile
    assert "'ODBC Driver 18 for SQL Server' in pyodbc.drivers()" in dockerfile
    assert "import pyodbc, pypdfium2, PIL" in dockerfile
    assert "capex-demo-v2-adf --help" in dockerfile
    assert "capex-demo-v2-review --help" in dockerfile
    assert "USER capex" in dockerfile
