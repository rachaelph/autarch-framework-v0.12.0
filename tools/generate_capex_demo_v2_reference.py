"""Generate governed TaskMaster and policy artifacts for CapEx Demo V2."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

from autarch.capex.demo_v2_reference import (
    ReferenceManifest,
    build_demo_policy_snapshot,
    build_task_master_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "Task_Type_Export_9_6_2026.xlsx"
)
DEFAULT_OUTPUT = (
    ROOT
    / "examples"
    / "data"
    / "capex"
    / "reference-v2"
)
TASK_MASTER_FILE = "task-master-us-v2.json"
POLICY_FILE = "policy-config-v2.json"
MANIFEST_FILE = "reference-manifest.json"


def _load_seed_generator() -> ModuleType:
    path = Path(__file__).with_name(
        "generate_capex_task_master_seed.py"
    )
    specification = importlib.util.spec_from_file_location(
        "generate_capex_task_master_seed",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("Unable to load TaskMaster seed generator")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: Dict[str, Any]) -> str:
    encoded = (
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return hashlib.sha256(encoded).hexdigest()


def generate(
    *,
    source_path: Path,
    output_dir: Path,
) -> ReferenceManifest:
    """Generate and validate all CICT-232 reference artifacts."""
    seed = _load_seed_generator()
    source_sha256 = hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest().upper()
    if source_sha256 != seed.EXPECTED_SHA256:
        raise ValueError(
            "TaskMaster SHA-256 mismatch: "
            f"expected {seed.EXPECTED_SHA256}, found {source_sha256}"
        )

    rows = seed.read_us_task_rows(source_path)
    seed.validate_rows(rows)
    task_master = build_task_master_snapshot(
        rows,
        source_file=seed.SOURCE_FILE,
        source_sheet=seed.SOURCE_SHEET,
        source_version=seed.SOURCE_VERSION,
        source_sha256=source_sha256,
    )
    policy = build_demo_policy_snapshot()

    output_dir.mkdir(parents=True, exist_ok=True)
    task_master_sha256 = _write_json(
        output_dir / TASK_MASTER_FILE,
        task_master.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
        ),
    )
    policy_sha256 = _write_json(
        output_dir / POLICY_FILE,
        policy.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
        ),
    )
    manifest = ReferenceManifest(
        task_master_artifact=TASK_MASTER_FILE,
        task_master_sha256=task_master_sha256,
        policy_artifact=POLICY_FILE,
        policy_sha256=policy_sha256,
        governed_task_count=task_master.active_task_count,
        policy_id=policy.policy_id,
        unresolved_confirmation_count=len(
            policy.unresolved_confirmation_ids
        ),
    )
    _write_json(
        output_dir / MANIFEST_FILE,
        manifest.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
        ),
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate CapEx Demo V2 reference artifacts",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()
    manifest = generate(
        source_path=args.source,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "outputDir": str(args.output_dir),
                "governedTaskCount": manifest.governed_task_count,
                "policyId": manifest.policy_id,
                "unresolvedConfirmationCount": (
                    manifest.unresolved_confirmation_count
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
