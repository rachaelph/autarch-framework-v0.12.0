"""Generate reviewed CapEx Demo V2 normalization artifacts."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from autarch.capex.demo_v2_normalization import (
    DemoContextOverlay,
    NormalizedInvoicePackage,
    normalize_abbyy_for_demo_v2,
    normalize_jc_workbook,
)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise argparse.ArgumentTypeError(
            "--created-at-utc must include a UTC offset"
        )
    return parsed


def _load_overlays(path: Path) -> Dict[str, DemoContextOverlay]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    overlays = [
        DemoContextOverlay.model_validate(item)
        for item in payload["overlays"]
    ]
    result = {
        overlay.target_invoice_id: overlay
        for overlay in overlays
    }
    if len(result) != len(overlays):
        raise ValueError(
            "context overlays must have unique targetInvoiceId values"
        )
    return result


def _one(source_dir: Path, pattern: str) -> Path:
    matches = sorted(source_dir.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one source matching {pattern!r}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _write_model(path: Path, model: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        model.model_dump_json(
            by_alias=True,
            exclude_none=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def generate(
    *,
    source_dir: Path,
    output_dir: Path,
    overlays_path: Path,
    run_id: str,
    created_at_utc: datetime,
) -> Dict[str, Any]:
    """Normalize the three target sources and classify the companion JC file."""
    overlays = _load_overlays(overlays_path)
    bruegmann = normalize_abbyy_for_demo_v2(
        _one(source_dir, "Bruegmann*.zip"),
        run_id=run_id,
        created_at_utc=created_at_utc,
        context_overlay=overlays[
            "135d03ac-3598-498a-83fe-d273d046bfea"
        ],
    )
    kimley = normalize_abbyy_for_demo_v2(
        _one(source_dir, "Kimley*.zip"),
        run_id=run_id,
        created_at_utc=created_at_utc,
        context_overlay=overlays[
            "58aceec5-f35a-48a7-8faa-2f1a195af203"
        ],
    )
    jc_target = normalize_jc_workbook(
        _one(source_dir, "JC2000A07814*.xlsx"),
        run_id=run_id,
        created_at_utc=created_at_utc,
        context_overlays={
            "JC2000A07814:20294": overlays[
                "JC2000A07814:20294"
            ]
        },
    )
    jc_companion = normalize_jc_workbook(
        _one(source_dir, "JC2000A07465*.xlsx"),
        run_id=run_id,
        created_at_utc=created_at_utc,
    )

    artifacts = {
        "bruegmann.normalized.json": bruegmann,
        "kimley-horn.normalized.json": kimley,
        "jc2000a07814.normalized.json": jc_target,
        "jc2000a07465.classification.json": jc_companion,
    }
    for file_name, model in artifacts.items():
        _write_model(output_dir / file_name, model)

    target_packages: List[NormalizedInvoicePackage] = [
        bruegmann,
        kimley,
        *jc_target.packages,
    ]
    manifest: Dict[str, Any] = {
        "schemaVersion": "1.0",
        "runId": run_id,
        "createdAtUtc": created_at_utc.isoformat(),
        "overlayFile": overlays_path.name,
        "targetPackages": [
            {
                "packageId": package.package_id,
                "invoiceId": package.invoice_id,
                "invoiceNumber": package.invoice_number,
                "normalizationStatus": (
                    package.normalization_status.value
                ),
                "artifact": next(
                    name
                    for name, model in artifacts.items()
                    if (
                        model is package
                        or (
                            hasattr(model, "packages")
                            and package in model.packages
                        )
                    )
                ),
                "sourceSha256": package.source.sha256,
                "syntheticOverlay": (
                    package.context_overlay is not None
                    and package.context_overlay.synthetic
                ),
                "reviewReasons": (
                    package.data_quality.review_reasons
                ),
            }
            for package in target_packages
        ],
        "companionClassification": {
            "artifact": "jc2000a07465.classification.json",
            "includedInvoiceCount": len(jc_companion.packages),
            "excludedRecordCount": len(
                jc_companion.excluded_records
            ),
        },
    }
    manifest_path = output_dir / "manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize CapEx Demo V2 invoice sources",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("examples/data/capex"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("examples/data/capex/normalized-v2"),
    )
    parser.add_argument(
        "--overlays",
        type=Path,
        default=Path(
            "examples/data/capex/demo-v2-context-overlays.json"
        ),
    )
    parser.add_argument(
        "--run-id",
        default="capex-demo-v2-normalization-2026-10-30",
    )
    parser.add_argument(
        "--created-at-utc",
        type=_parse_utc,
        default=_parse_utc("2026-10-30T00:00:00Z"),
    )
    args = parser.parse_args()
    manifest = generate(
        source_dir=args.source_dir,
        output_dir=args.output_dir,
        overlays_path=args.overlays,
        run_id=args.run_id,
        created_at_utc=args.created_at_utc,
    )
    print(
        json.dumps(
            {
                "outputDir": str(args.output_dir),
                "targetPackageCount": len(
                    manifest["targetPackages"]
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
