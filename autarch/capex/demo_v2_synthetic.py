"""Traceable synthetic-input helpers for CapEx Demo V2."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .demo_v2_normalization import NormalizedInvoicePackage


_REQUIRED_INPUT_FIELDS = {
    "assumptionId",
    "category",
    "value",
    "affectedRecords",
    "rationale",
    "circleKQuestion",
    "replacementData",
    "status",
}


def validate_synthetic_register(payload: Any) -> Dict[str, object]:
    """Validate the assumption register needed for an opt-in synthetic run."""
    if not isinstance(payload, dict):
        raise ValueError("Synthetic input register must be a JSON object.")
    if payload.get("schemaVersion") != "2.0-synthetic-input-register":
        raise ValueError("Unsupported synthetic input register schema.")
    inputs = payload.get("syntheticInputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Synthetic input register must contain inputs.")
    assumption_ids = [
        item.get("assumptionId")
        for item in inputs
        if isinstance(item, dict)
    ]
    if len(assumption_ids) != len(inputs):
        raise ValueError("Every synthetic input must be a JSON object.")
    if len(assumption_ids) != len(set(assumption_ids)):
        raise ValueError(
            "Synthetic input register contains duplicate assumption IDs."
        )
    for item in inputs:
        if not isinstance(item, dict) or not _REQUIRED_INPUT_FIELDS <= set(item):
            raise ValueError(
                "Every synthetic input requires traceability and a Circle K "
                "replacement question."
            )
    setting_ids = payload.get("syntheticSettingIds")
    if not isinstance(setting_ids, list) or not all(
        isinstance(value, str) and value.strip()
        for value in setting_ids
    ):
        raise ValueError(
            "Synthetic input register requires syntheticSettingIds."
        )
    return payload


def synthetic_setting_ids(
    register: Mapping[str, object],
) -> List[str]:
    """Return the explicitly enabled synthetic policy setting IDs."""
    values = register.get("syntheticSettingIds")
    if not isinstance(values, list):
        raise ValueError("syntheticSettingIds must be a list.")
    return [str(value) for value in values]


def apply_synthetic_line_values(
    package: NormalizedInvoicePackage,
    register: Mapping[str, object],
) -> tuple[NormalizedInvoicePackage, List[str]]:
    """Apply registered line-value overlays without mutating source packages."""
    inputs = register.get("syntheticInputs")
    if not isinstance(inputs, Sequence):
        raise ValueError("Synthetic input register must contain inputs.")
    override = next(
        (
            item
            for item in inputs
            if isinstance(item, dict)
            and item.get("assumptionId") == "SYN-DATA-001"
        ),
        None,
    )
    if override is None:
        return package, []
    affected = override.get("affectedRecords")
    if not isinstance(affected, list):
        raise ValueError(
            "SYN-DATA-001 affectedRecords must be a list."
        )
    affected_ids = {str(value) for value in affected}
    applicable_ids = affected_ids & {
        line.line_id for line in package.lines
    }
    if not applicable_ids:
        return package, []
    lines = [
        line.model_copy(
            update={
                "quantity": 1.0,
                "unit_cost": line.line_amount,
            }
        )
        if line.line_id in applicable_ids
        else line
        for line in package.lines
    ]
    return (
        package.model_copy(update={"lines": lines}),
        ["SYN-DATA-001"],
    )


__all__ = [
    "apply_synthetic_line_values",
    "synthetic_setting_ids",
    "validate_synthetic_register",
]
