"""Purchase-order-backed Nuvolo recognition, independent of accounting."""
from __future__ import annotations

import re
from typing import Mapping

from pydantic import Field

from .demo_v2_contracts import DemoV2Model


_PREFIX = re.compile(r"FWKD|FFX", re.IGNORECASE)
_WORK_ORDER = re.compile(r"(?<![\w])(?:FWKD|FFX)(?:[ \t]*[-:#]?[ \t]*)([0-9]{1,128})(?![\w]|[-.][\w])", re.IGNORECASE)
_PO_FIELDS = {
    "po", "ponumber", "purchaseorder", "purchaseordernumber",
    "customerpo", "customerponumber", "customerpurchaseorder", "customerpurchaseordernumber",
}


class NuvoloContext(DemoV2Model):
    work_order_no: str | None = Field(default=None, pattern=r"^[0-9]{1,128}$")
    risk_flag: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)


def resolve_nuvolo_work_order(references: Mapping[str, str]) -> NuvoloContext:
    numbers: set[str] = set()
    evidence: dict[str, str] = {}
    reasons: list[str] = []
    for field, value in references.items():
        name = re.sub(r"[^a-z0-9]", "", field.rsplit(":", 1)[-1].casefold())
        if name not in _PO_FIELDS:
            continue
        original_po = value.lstrip()
        prefixes = list(_PREFIX.finditer(original_po))
        if not prefixes or not (
            original_po.upper().startswith("FFX")
            or any(prefix.group().upper() == "FWKD" for prefix in prefixes)
        ):
            continue
        evidence[field] = value
        for prefix in prefixes:
            match = _WORK_ORDER.match(original_po, prefix.start())
            if match is None:
                reason = f"Malformed {prefix.group().upper()} reference in {field}; verify the original work order."
                if reason not in reasons:
                    reasons.append(reason)
            else:
                numbers.add(match.group(1))
    if len(numbers) > 1:
        reasons.append("Conflicting Nuvolo work-order numbers; no single Nuvolo work order was selected.")
    return NuvoloContext(
        work_order_no=next(iter(numbers)) if len(numbers) == 1 and not reasons else None,
        risk_flag=bool(evidence),
        review_reasons=reasons, evidence=evidence,
    )
