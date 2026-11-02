from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from autarch.capex.demo_v2_reference import (
    ApprovalStatus,
    BundlingPolicy,
    DemoPolicySnapshot,
    ReferenceManifest,
    ReviewBehavior,
    TaskMasterSnapshot,
    build_demo_policy_snapshot,
    load_demo_policy_snapshot,
    load_task_master_snapshot,
    rank_task_candidates,
)


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = (
    ROOT / "examples" / "data" / "capex" / "reference-v2"
)
GENERATOR_PATH = (
    ROOT / "tools" / "generate_capex_demo_v2_reference.py"
)
EXPECTED_SOURCE_SHA256 = (
    "CC41B492A5DE454EFD7405066EF1EAA6CDE1FF050C8E71ED180831197B6EBA13"
)


def _load_generator() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "generate_capex_demo_v2_reference",
        GENERATOR_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_committed_task_master_snapshot_is_governed() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )

    assert snapshot.schema_version == "2.0-task-master"
    assert snapshot.region_code == "US"
    assert snapshot.total_source_rows == 761
    assert snapshot.active_task_count == 761
    assert len(snapshot.tasks) == 761
    assert snapshot.source.sha256 == EXPECTED_SOURCE_SHA256
    assert len({task.task_code for task in snapshot.tasks}) == 761
    assert all(task.active for task in snapshot.tasks)
    assert all(task.search_text for task in snapshot.tasks)


def test_task_snapshot_retains_governed_retrieval_metadata() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )
    first = snapshot.tasks[0]

    assert first.task_code == "0000"
    assert first.description == "ENGINEERING/PERMITS/OVERHEAD"
    assert first.asset_category_major == "STORE EQUIPMENT"
    assert first.asset_category_minor == "ENG/PERM"
    assert first.property_type == "Real"
    assert (
        first.search_text
        == "0000 engineering permits overhead store equipment eng perm real"
    )


def test_candidate_retrieval_is_deterministic_and_governed() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )

    first = rank_task_candidates(
        "environmental assessment professional services",
        snapshot,
        limit=3,
    )
    second = rank_task_candidates(
        "environmental assessment professional services",
        snapshot,
        limit=3,
    )

    assert [task.task_code for task in first] == ["0007"]
    assert [task.task_code for task in second] == ["0007"]
    governed_codes = {task.task_code for task in snapshot.tasks}
    assert all(task.task_code in governed_codes for task in first)


def test_candidate_retrieval_rejects_invalid_limit() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )

    with pytest.raises(ValueError, match="at least 1"):
        rank_task_candidates("equipment", snapshot, limit=0)


def test_candidate_retrieval_ignores_numeric_dimensions() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )

    candidates = rank_task_candidates(
        'Infostrip 10PK length 27" 685.8mm',
        snapshot,
        limit=10,
    )

    assert candidates == []


def test_candidate_retrieval_ignores_generic_product_attributes() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )

    candidates = rank_task_candidates(
        "Non-Anodized black front retainer depth width color",
        snapshot,
        limit=10,
    )

    assert candidates == []


def test_jc_source_codes_are_not_synthesized_into_us_task_master() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )
    task_codes = {task.task_code for task in snapshot.tasks}

    assert {
        "0303",
        "0601",
        "0604",
        "1201",
        "3307",
    }.isdisjoint(task_codes)


def test_committed_policy_snapshot_is_explicitly_unapproved() -> None:
    policy = load_demo_policy_snapshot(
        REFERENCE_DIR / "policy-config-v2.json"
    )

    assert policy.schema_version == "2.0-policy"
    assert policy.approval_status == (
        ApprovalStatus.PROPOSED_DEMO_ASSUMPTION
    )
    assert policy.capitalization.base_threshold == 1000
    assert policy.capitalization.threshold_basis == "UNIT_COST"
    assert policy.capitalization.below_threshold_exception_enabled is False
    assert policy.capitalization.non_usd_behavior == ReviewBehavior.REVIEW
    assert policy.bundling.grouping_key == [
        "TASK_CODE",
        "SITE_ID",
        "PROJECT_ID",
        "CURRENCY",
    ]
    assert policy.bundling.historical_lookback_enabled is False
    assert policy.unresolved_confirmation_ids


def test_policy_requires_review_for_unconfirmed_and_jc_inputs() -> None:
    policy = build_demo_policy_snapshot()

    settings = {
        setting.setting_id: setting
        for setting in policy.settings
    }
    assert settings["base-threshold"].status == (
        ApprovalStatus.POLICY_SUPPORTED
    )
    assert settings["threshold-basis"].status == (
        ApprovalStatus.BUSINESS_CONFIRMATION_REQUIRED
    )
    assert settings["nature-rule-library"].value == "NOT_SUPPLIED"
    assert policy.default_unresolved_behavior == ReviewBehavior.REVIEW
    assert (
        policy.jc_reference_handling.governed_mapping_available
        is False
    )
    assert policy.jc_reference_handling.result_without_mapping == (
        ReviewBehavior.REVIEW
    )
    assert policy.jc_reference_handling.included_transaction_types == [
        "AI"
    ]
    assert set(
        policy.jc_reference_handling.excluded_transaction_types
    ) == {"OV", "JP"}


def test_bundling_contract_rejects_incomplete_key() -> None:
    with pytest.raises(ValidationError, match="groupingKey"):
        BundlingPolicy(
            grouping_key=["TASK_CODE", "PROJECT_ID", "CURRENCY"],
            minimum_candidate_members=2,
        )


def test_policy_contract_rejects_unknown_unresolved_setting() -> None:
    payload = build_demo_policy_snapshot().model_dump()
    payload["unresolved_confirmation_ids"].append("unknown-setting")

    with pytest.raises(
        ValidationError,
        match="unresolvedConfirmationIds",
    ):
        DemoPolicySnapshot.model_validate(payload)


def test_reference_manifest_matches_committed_artifacts() -> None:
    manifest = ReferenceManifest.model_validate_json(
        (REFERENCE_DIR / "reference-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    task_bytes = (
        REFERENCE_DIR / manifest.task_master_artifact
    ).read_bytes()
    policy_bytes = (
        REFERENCE_DIR / manifest.policy_artifact
    ).read_bytes()

    assert manifest.governed_task_count == 761
    assert manifest.unresolved_confirmation_count == 8
    assert hashlib.sha256(task_bytes).hexdigest() == (
        manifest.task_master_sha256
    )
    assert hashlib.sha256(policy_bytes).hexdigest() == (
        manifest.policy_sha256
    )


def test_reference_generation_is_deterministic(
    tmp_path: Path,
) -> None:
    generator = _load_generator()
    source = (
        ROOT
        / "examples"
        / "data"
        / "capex"
        / "Task_Type_Export_9_6_2026.xlsx"
    )

    first = tmp_path / "first"
    second = tmp_path / "second"
    generator.generate(source_path=source, output_dir=first)
    generator.generate(source_path=source, output_dir=second)

    assert {
        path.name: path.read_bytes()
        for path in first.iterdir()
    } == {
        path.name: path.read_bytes()
        for path in second.iterdir()
    }
    assert json.loads(
        (first / "reference-manifest.json").read_text(
            encoding="utf-8"
        )
    ) == json.loads(
        (
            REFERENCE_DIR / "reference-manifest.json"
        ).read_text(encoding="utf-8")
    )


def test_task_snapshot_contract_rejects_count_mismatch() -> None:
    snapshot = load_task_master_snapshot(
        REFERENCE_DIR / "task-master-us-v2.json"
    )
    payload = snapshot.model_dump()
    payload["active_task_count"] = 760

    with pytest.raises(ValidationError, match="activeTaskCount"):
        TaskMasterSnapshot.model_validate(payload)
