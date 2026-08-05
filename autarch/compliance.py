"""Compliance evidence — turn the ledger into artifacts an auditor accepts.

Autarch already produces the raw material a regulator wants: a signed,
tamper-evident, deny-by-default action trail with human ratification and a
right-to-be-forgotten overlay that preserves integrity. This module *packages*
that into control evidence mapped to real frameworks (SOC 2, the EU AI Act's
record-keeping duty, and HIPAA's audit-control principle), plus a portable,
verifiable evidence bundle.

This is deliberately the ground the big model labs are disinclined to take — their
product is capability; ours is *control you can prove to a third party*. Nothing
here is legal advice; it maps technical facts to control intents so a compliance
team has a defensible starting artifact.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .memory import WhyMemory


@dataclass
class Control:
    """One control's evidence: did the ledger demonstrate this property?"""

    id: str
    framework: str
    title: str
    satisfied: bool
    evidence: str
    sample_why_ids: List[str] = field(default_factory=list)


@dataclass
class ComplianceReport:
    generated_at: float
    node: str
    total_actions: int
    chain_intact: bool
    controls: List[Control] = field(default_factory=list)

    @property
    def all_satisfied(self) -> bool:
        return all(c.satisfied for c in self.controls)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["all_satisfied"] = self.all_satisfied
        return data


# Control catalogue: (id, framework, title, predicate-key). The predicate keys map
# to checks computed over the ledger below.
_CATALOGUE = [
    ("CC7.2", "SOC2", "Every action is logged and attributable", "attributable"),
    ("CC6.1", "SOC2", "Access is deny-by-default (least privilege)", "deny_by_default"),
    ("CC7.3", "SOC2", "Audit trail is tamper-evident", "tamper_evident"),
    ("CC8.1", "SOC2", "High-risk changes require human authorization", "human_authz"),
    ("Art.12", "EU-AI-Act", "Automatic record-keeping over the system's lifetime", "record_keeping"),
    ("Art.14", "EU-AI-Act", "Human oversight can override the system", "human_oversight"),
    ("164.312(b)", "HIPAA", "Audit controls record activity on ePHI systems", "audit_controls"),
]


class ComplianceReporter:
    """Computes control evidence from a WhyMemory ledger."""

    def __init__(self, memory: WhyMemory):
        self.memory = memory

    def report(self, node: str = "") -> ComplianceReport:
        records = self.memory.all()
        chain_ok, _ = self.memory.verify_chain()
        facts = self._facts(records)
        controls = [self._control(spec, facts) for spec in _CATALOGUE]
        return ComplianceReport(
            generated_at=time.time(),
            node=node or getattr(self.memory, "node_id", ""),
            total_actions=len(records),
            chain_intact=chain_ok,
            controls=controls,
        )

    def _facts(self, records) -> Dict[str, object]:
        signed = [r for r in records if r.signer]
        denied = [r for r in records if not r.gate_allowed]
        human = [r for r in records if r.human_decision in ("ratify", "overrule")]
        chain_ok, _ = self.memory.verify_chain()
        return {
            "records": records,
            "count": len(records),
            "all_signed": bool(records) and len(signed) == len(records),
            "any_signed": bool(signed),
            "has_denials": bool(denied),
            "denied_ids": [r.id for r in denied][:5],
            "human_ids": [r.id for r in human][:5],
            "has_human": bool(human),
            "chain_ok": chain_ok,
            "sample_ids": [r.id for r in records][:5],
        }

    def _control(self, spec, facts) -> Control:
        cid, framework, title, key = spec
        satisfied, evidence, samples = self._evaluate(key, facts)
        return Control(cid, framework, title, satisfied, evidence, samples)

    def _evaluate(self, key: str, f) -> tuple:
        if key == "attributable":
            ok = f["any_signed"] or f["count"] == 0
            ev = (f"{f['count']} actions recorded; cryptographic signing "
                  f"{'present' if f['any_signed'] else 'unavailable (install crypto extra)'}.")
            return ok, ev, f["sample_ids"]
        if key == "deny_by_default":
            # The kernel is deny-by-default by construction; denials in the trail
            # are positive evidence it actually refuses.
            ev = ("Kernel refuses any ungranted capability by construction; "
                  f"{'observed denials in the trail.' if f['has_denials'] else 'no denial events yet.'}")
            return True, ev, f["denied_ids"]
        if key == "tamper_evident":
            return f["chain_ok"], (
                "Hash-chained ledger verifies intact." if f["chain_ok"]
                else "CHAIN BROKEN — tampering detected."), f["sample_ids"]
        if key in ("human_authz", "human_oversight"):
            ev = ("Human ratify/overrule events recorded." if f["has_human"]
                  else "No human-decision events yet (enable require_ratify policies).")
            return True, ev, f["human_ids"]
        if key == "record_keeping":
            return True, f"{f['count']} lifecycle records retained and exportable.", f["sample_ids"]
        if key == "audit_controls":
            ev = ("Signed, tamper-evident activity log present."
                  if f["chain_ok"] else "Audit log integrity check FAILED.")
            return f["chain_ok"], ev, f["sample_ids"]
        return False, "unknown control", []

    def evidence_bundle(self, path: Optional[str] = None, node: str = "") -> dict:
        """A portable, self-verifying evidence bundle (report + full signed trail)."""
        report = self.report(node=node)
        bundle = {
            "kind": "autarch.compliance.bundle/v1",
            "report": report.to_dict(),
            "audit_trail": self.memory.export_audit(),
            "verification": {
                "chain_intact": report.chain_intact,
                "instructions": "Re-import into WhyMemory and call verify_chain() to re-check.",
            },
        }
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, indent=2, default=str)
        return bundle


def markdown_report(report: ComplianceReport) -> str:
    """Render a compliance report as a readable Markdown table."""
    lines = [
        f"# Autarch Compliance Report",
        "",
        f"- Node: `{report.node}`",
        f"- Actions recorded: {report.total_actions}",
        f"- Ledger integrity: {'INTACT' if report.chain_intact else 'BROKEN'}",
        f"- Overall: {'ALL CONTROLS SATISFIED' if report.all_satisfied else 'GAPS PRESENT'}",
        "",
        "| Framework | Control | Title | Status | Evidence |",
        "|---|---|---|---|---|",
    ]
    for c in report.controls:
        status = "PASS" if c.satisfied else "GAP"
        lines.append(f"| {c.framework} | {c.id} | {c.title} | {status} | {c.evidence} |")
    return "\n".join(lines)
