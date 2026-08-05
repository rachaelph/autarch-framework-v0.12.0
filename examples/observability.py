"""Operability & compliance: observability, audit export, RTBF, health.

The production "turn code into a product" surface:
  - a durable JSON-lines event stream (stdlib),
  - an audit-trail export that preserves integrity proofs,
  - right-to-be-forgotten redaction that masks PII WITHOUT breaking the hash chain,
  - a health/readiness report for container probes.

Run from the repo root:
    python examples/observability.py
"""
import io
import json
import shutil
from pathlib import Path

from autarch import Agent, JsonlSink, capability, health_check


def banner(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main():
    ws = Path("./sandbox/_ops")
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)

    banner("1) Structured event stream (durable JSON lines)")
    buf = io.StringIO()
    agent = Agent(
        intent="create customer.txt that says SSN 123-45-6789",
        council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=ws, events=JsonlSink(stream=buf),
    )
    result = agent.run()
    for line in buf.getvalue().splitlines():
        e = json.loads(line)
        print(f"  {e['kind']:24s} {e['data']}")

    banner("2) Right-to-be-forgotten — redact PII, keep the integrity proof")
    print(f"  before: intent = {agent.memory.get(result.why_id).intent_text!r}")
    print(f"  ledger verifies: {agent.memory.verify_chain()[0]}")
    agent.memory.redact(result.why_id, reason="GDPR erasure request")
    print(f"  after:  intent = {agent.memory.get(result.why_id).intent_text!r}")
    print(f"  ledger STILL verifies: {agent.memory.verify_chain()[0]}  (sealed payload untouched)")
    print(f"  provenance still verifies: {agent.memory.verify_provenance(result.why_id)}")

    banner("3) Audit-trail export (regulator-grade)")
    rows = agent.memory.export_audit(ws / "audit.jsonl")
    print(f"  exported {len(rows)} record(s) to audit.jsonl")
    print(f"  each row carries: {sorted(rows[0].keys())}")
    print(f"  redacted fields recorded: {rows[0]['redacted_fields']}")

    banner("4) Health / readiness (for container probes)")
    h = health_check(ws)
    print(f"  status: {h['status'].upper()}   version: {h['version']}")
    for name, check in h["checks"].items():
        print(f"    {name:10s} {check}")
    print("\nObservable, exportable, forgettable, and probe-able \u2014 without losing the integrity proof.")


if __name__ == "__main__":
    main()
