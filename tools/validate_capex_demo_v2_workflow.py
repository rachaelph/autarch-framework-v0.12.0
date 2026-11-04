"""Validate the complete three-invoice CapEx Demo V2 workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from autarch.capex.demo_v2_capitalization import CapitalizationRuleSet
from autarch.capex.demo_v2_contracts import (
    DemoWorkflowRequest,
    InvoicePackageReference,
    TaskDetermination,
)
from autarch.capex.demo_v2_job import run_demo_v2_workflow
from autarch.capex.demo_v2_normalization import (
    JCWorkbookNormalizationResult,
    NormalizedInvoicePackage,
)
from autarch.capex.demo_v2_reference import (
    canonical_sha256,
    load_demo_policy_snapshot,
    load_task_master_snapshot,
)
from autarch.capex.demo_v2_synthetic import (
    apply_synthetic_line_values,
    synthetic_setting_ids,
    validate_synthetic_register,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data" / "capex"
NORMALIZED = DATA / "normalized-v2"
REFERENCE = DATA / "reference-v2"
RESULTS = DATA / "results-v2"
DEFAULT_OUTPUT = RESULTS / "workflow.synthetic-validation.json"
NOW = datetime(2026, 11, 4, 17, 0, tzinfo=timezone.utc)


class RecordingCursor:
    def __init__(self, connection: "RecordingConnection") -> None:
        self.connection = connection
        self.operation = ""
        self.parameters: tuple[Any, ...] = ()

    def execute(
        self,
        operation: str,
        parameters: Sequence[Any] = (),
    ) -> "RecordingCursor":
        self.operation = operation
        self.parameters = tuple(parameters)
        if "INSERT INTO [capex].[AssessmentRun]" in operation:
            values = self.parameters
            self.connection.assessment_runs[str(values[0])] = (
                str(values[1]),
                int(values[2]),
                int(values[3]),
                int(values[4]),
            )
            self.connection.insert_count += 1
        elif "INSERT INTO" in operation:
            self.connection.insert_count += 1
        return self

    def executemany(
        self,
        operation: str,
        values: Iterable[Sequence[Any]],
    ) -> "RecordingCursor":
        rows = [tuple(row) for row in values]
        if "INSERT INTO [task].[TaskSuggestion]" in operation:
            for row in rows:
                self.connection.task_rows.setdefault(
                    str(row[4]),
                    [],
                ).append(
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[5],
                        row[6],
                        row[8],
                    )
                )
        self.connection.insert_count += len(rows)
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        if "FROM [pub].[v1_TaskMaster]" in self.operation:
            return [(code,) for code in self.connection.task_codes]
        if "FROM [task].[TaskSuggestion]" in self.operation:
            event_id = str(self.parameters[0])
            return sorted(
                self.connection.task_rows.get(event_id, []),
                key=lambda row: str(row[4]),
            )
        return []

    def fetchone(self) -> tuple[Any, ...] | None:
        if "FROM [capex].[AssessmentRun]" in self.operation:
            return self.connection.assessment_runs.get(
                str(self.parameters[0])
            )
        return None

    def close(self) -> None:
        return None


class RecordingConnection:
    def __init__(self, task_codes: Sequence[str]) -> None:
        self.task_codes = tuple(task_codes)
        self.task_rows: dict[str, list[tuple[Any, ...]]] = {}
        self.assessment_runs: dict[str, tuple[Any, ...]] = {}
        self.insert_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


def _load_targets() -> tuple[
    list[NormalizedInvoicePackage],
    dict[str, list[TaskDetermination]],
    int,
]:
    bruegmann = NormalizedInvoicePackage.model_validate_json(
        (NORMALIZED / "bruegmann.normalized.json").read_text(
            encoding="utf-8"
        )
    )
    kimley = NormalizedInvoicePackage.model_validate_json(
        (NORMALIZED / "kimley-horn.normalized.json").read_text(
            encoding="utf-8"
        )
    )
    jc = JCWorkbookNormalizationResult.model_validate_json(
        (NORMALIZED / "jc2000a07814.normalized.json").read_text(
            encoding="utf-8"
        )
    ).packages[0]

    bruegmann_payload = json.loads(
        (RESULTS / "bruegmann.task-suggestions.json").read_text(
            encoding="utf-8"
        )
    )
    kimley_payload = json.loads(
        (RESULTS / "kimley-horn.task-suggestions.json").read_text(
            encoding="utf-8"
        )
    )
    jc_payload = json.loads(
        (RESULTS / "jc.task-reviews.json").read_text(encoding="utf-8")
    )
    task_payloads = {
        bruegmann.package_id: bruegmann_payload["taskDeterminations"],
        kimley.package_id: kimley_payload["taskDeterminations"],
        jc.package_id: jc_payload["target"]["packages"][0][
            "taskDeterminations"
        ],
    }
    task_results = {
        package_id: [
            TaskDetermination.model_validate(item)
            for item in values
        ]
        for package_id, values in task_payloads.items()
    }
    prior_model_calls = int(bruegmann_payload["modelCallCount"]) + int(
        kimley_payload["modelCallCount"]
    )
    return [bruegmann, kimley, jc], task_results, prior_model_calls


def _request(
    packages: Sequence[NormalizedInvoicePackage],
    *,
    workflow_run_id: str,
    allow_synthetic_inputs: bool,
    persist_results: bool,
) -> DemoWorkflowRequest:
    return DemoWorkflowRequest(
        workflowRunId=workflow_run_id,
        requestedAtUtc=NOW,
        packages=[
            InvoicePackageReference(
                packageId=package.package_id,
                runId=package.run_id,
                packageUri=(
                    NORMALIZED
                    / (
                        "jc2000a07814.normalized.json"
                        if package.invoice_id == "JC2000A07814"
                        else (
                            "bruegmann.normalized.json"
                            if package.vendor_name.upper().startswith(
                                "BRUEGMANN"
                            )
                            else "kimley-horn.normalized.json"
                        )
                    )
                ).as_uri(),
                expectedInvoiceId=package.invoice_id,
                expectedSourceSha256=package.source.sha256,
            )
            for package in packages
        ],
        taskRegion="US",
        modelDeployment="gpt-5.1",
        taskPromptVersion="capex-demo-v2-task-suggestion-v1",
        capitalizationPolicyVersion="circle-k-na-demo-unapproved-2026-10-30",
        bundlingPolicyVersion="circle-k-na-demo-unapproved-2026-10-30",
        persistResults=persist_results,
        failOnReview=False,
        allowSyntheticInputs=allow_synthetic_inputs,
    )


def _resolver(
    task_results: Mapping[str, Sequence[TaskDetermination]],
):
    def resolve(
        package: NormalizedInvoicePackage,
        request: DemoWorkflowRequest,
    ) -> Sequence[TaskDetermination]:
        return [
            item.model_copy(
                update={
                    "workflow_run_id": request.workflow_run_id,
                    "created_at_utc": NOW,
                }
            )
            for item in task_results[package.package_id]
        ]

    return resolve


def _validate_counts(result: Any, *, synthetic: bool) -> None:
    if result.status != "SUCCEEDED":
        raise ValueError(f"Workflow status was {result.status}: {result.errors}")
    if len(result.packages) != 3:
        raise ValueError("Expected three package summaries.")
    if sum(item.line_count for item in result.packages) != 23:
        raise ValueError("Expected exactly 23 invoice lines.")
    if len(result.task_determinations) != 23:
        raise ValueError("Expected one task determination per line.")
    if len(result.assessments) != 23:
        raise ValueError("Expected one capitalization assessment per line.")
    if synthetic:
        recommendations = Counter(
            item.recommendation.value for item in result.assessments
        )
        if recommendations != Counter(
            {"OPEX": 11, "CAPEX": 7, "REVIEW": 5}
        ):
            raise ValueError(
                f"Unexpected synthetic recommendations: {recommendations}"
            )
        if len(result.bundle_groups) != 1:
            raise ValueError("Expected exactly one synthetic bundle group.")
        if sum(
            len(group.members) for group in result.bundle_groups
        ) != 9:
            raise ValueError("Expected 9 synthetic bundle members.")
        if any(group.total_amount <= 0 for group in result.bundle_groups):
            raise ValueError("Bundle groups must have positive combined value.")
    else:
        if any(
            item.recommendation.value != "REVIEW"
            for item in result.assessments
        ):
            raise ValueError(
                "Current-policy execution must remain review-only."
            )
        if result.bundle_groups:
            raise ValueError(
                "Current-policy execution must not create bundle groups."
            )


def validate_workflow() -> Dict[str, object]:
    packages, task_results, prior_model_calls = _load_targets()
    task_master = load_task_master_snapshot(
        REFERENCE / "task-master-us-v2.json"
    )
    policy = load_demo_policy_snapshot(
        REFERENCE / "policy-config-v2.json"
    )
    register = validate_synthetic_register(
        json.loads(
            (
                REFERENCE / "synthetic-input-register-v2.json"
            ).read_text(encoding="utf-8")
        )
    )
    rule_set = CapitalizationRuleSet.model_validate_json(
        (
            REFERENCE / "synthetic-capitalization-rules-v2.json"
        ).read_text(encoding="utf-8")
    )

    truthful = run_demo_v2_workflow(
        request=_request(
            packages,
            workflow_run_id="capex-demo-v2-current-policy-2026-11-04",
            allow_synthetic_inputs=False,
            persist_results=False,
        ),
        packages=packages,
        task_master=task_master,
        policy=policy,
        task_resolver=_resolver(task_results),
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )
    _validate_counts(truthful, synthetic=False)

    synthetic_packages = [
        apply_synthetic_line_values(package, register)[0]
        for package in packages
    ]
    connection = RecordingConnection(
        [task.task_code for task in task_master.tasks]
    )
    synthetic_request = _request(
        synthetic_packages,
        workflow_run_id="capex-demo-v2-synthetic-2026-11-04",
        allow_synthetic_inputs=True,
        persist_results=True,
    )
    synthetic = run_demo_v2_workflow(
        request=synthetic_request,
        packages=packages,
        assessment_packages=synthetic_packages,
        task_master=task_master,
        policy=policy,
        task_resolver=_resolver(task_results),
        rule_set=rule_set,
        synthetic_setting_ids=synthetic_setting_ids(register),
        connection=connection,
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )
    _validate_counts(synthetic, synthetic=True)
    if synthetic.persistence.model_dump() != {
        "task_suggestions": 23,
        "assessments": 23,
        "bundle_groups": 1,
        "bundle_members": 9,
    }:
        raise ValueError(
            f"Persistence counts did not reconcile: {synthetic.persistence}"
        )
    first_insert_count = connection.insert_count
    replay = run_demo_v2_workflow(
        request=synthetic_request,
        packages=packages,
        assessment_packages=synthetic_packages,
        task_master=task_master,
        policy=policy,
        task_resolver=_resolver(task_results),
        rule_set=rule_set,
        synthetic_setting_ids=synthetic_setting_ids(register),
        connection=connection,
        requested_at_utc=NOW,
        completed_at_utc=NOW,
    )
    _validate_counts(replay, synthetic=True)
    replay_insert_count = connection.insert_count - first_insert_count
    if replay_insert_count != 0:
        raise ValueError(
            f"Exact replay inserted {replay_insert_count} rows."
        )

    allowed_tasks = {task.task_code for task in task_master.tasks}
    invalid_tasks = sorted(
        {
            item.suggested_task_code
            for item in synthetic.task_determinations
            if item.suggested_task_code is not None
            and item.suggested_task_code not in allowed_tasks
        }
    )
    if invalid_tasks:
        raise ValueError(f"Invalid governed task codes: {invalid_tasks}")

    evidence = {
        "schemaVersion": "2.0-workflow-validation",
        "generatedAtUtc": NOW.isoformat(),
        "status": "PASS",
        "executionArchitecture": "DIRECT_CONTAINER_APPS_BATCH",
        "taskEvidence": {
            "mode": "REPLAY_OF_PRIOR_GOVERNED_RESULTS",
            "priorModelCalls": prior_model_calls,
            "modelCallsDuringWorkflowValidation": 0,
            "invalidTaskCodes": invalid_tasks,
        },
        "truthfulCurrentPolicy": truthful.model_dump(
            mode="json",
            by_alias=True,
        ),
        "syntheticDemo": {
            "warning": (
                "UNAPPROVED SYNTHETIC DEMO INPUTS; replace from the "
                "synthetic-input register before production use."
            ),
            "registerVersion": register["registerId"],
            "result": synthetic.model_dump(mode="json", by_alias=True),
        },
        "replayEvidence": {
            "status": replay.status.value,
            "additionalInsertCount": replay_insert_count,
            "logicalPersistenceCounts": replay.persistence.model_dump(
                mode="json",
                by_alias=True,
            ),
        },
        "recordingSqlEvidence": {
            "purpose": (
                "Exercise transaction and replay orchestration without "
                "claiming a live Azure SQL deployment."
            ),
            "firstRunInsertOperationCount": first_insert_count,
            "commitCountIncludingReplay": connection.commit_count,
            "rollbackCount": connection.rollback_count,
        },
    }
    evidence["sha256"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    evidence = validate_workflow()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "output": str(args.output),
                "sha256": evidence["sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
