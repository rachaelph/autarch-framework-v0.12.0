"""Tests for the compliance evidence exporter (ComplianceReporter)."""
from autarch.agent import Agent, capability
from autarch.compliance import ComplianceReporter, markdown_report


def _agent(tmp_path):
    a = Agent("gw", grants=[capability("file.write", scope={"path_prefix": "."})],
              workspace=str(tmp_path), auto_preside=False)
    a.enact("file.write", {"path": "a.txt", "content": "x"})
    a.enact("file.delete", {"path": "a.txt"})  # ungranted -> a denial in the trail
    return a


def test_report_controls_present(tmp_path):
    a = _agent(tmp_path)
    rep = ComplianceReporter(a.memory).report(node="local")
    frameworks = {c.framework for c in rep.controls}
    assert {"SOC2", "EU-AI-Act", "HIPAA"} <= frameworks
    assert rep.total_actions == 2
    assert rep.chain_intact


def test_tamper_evident_control_reflects_chain(tmp_path):
    a = _agent(tmp_path)
    rep = ComplianceReporter(a.memory).report()
    tamper = [c for c in rep.controls if c.id == "CC7.3"][0]
    assert tamper.satisfied is True


def test_markdown_report_renders(tmp_path):
    a = _agent(tmp_path)
    md = markdown_report(ComplianceReporter(a.memory).report())
    assert "Autarch Compliance Report" in md
    assert "| Framework |" in md


def test_evidence_bundle_is_self_describing(tmp_path):
    a = _agent(tmp_path)
    bundle = ComplianceReporter(a.memory).evidence_bundle(node="local")
    assert bundle["kind"].startswith("autarch.compliance.bundle")
    assert bundle["report"]["total_actions"] == 2
    assert len(bundle["audit_trail"]) == 2
