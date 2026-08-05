# Autarch — Quickstart

An AI-native operating layer where **intelligence is unlimited but consequences are
governed**. Self-contained: pure Python + stdlib SQLite, with `cryptography`
optional for AES-GCM and signing.

## Install

```bash
pip install -e ".[crypto]"     # crypto extra enables encrypted-at-rest keys + AES-GCM mesh
# or, zero dependencies:
pip install -e .
```

Run the test suite and the examples:

```bash
pytest
python examples/quickstart.py
```

## 60-second tour

```python
from autarch import Agent, capability

agent = Agent(
    intent="create notes.txt that says Hello Autarch",
    council=["mock"],                                  # or ["ollama:llama3"]
    grants=[capability("file.write", scope={"path_prefix": "."})],
    # no file.delete grant -> it literally cannot delete. Provable.
)
result = agent.run()
print(result.executed, result.why_id)
```

## The CLI

```bash
autarch do "create hello.txt that says hi"          # govern an intent
autarch why <why_id>                                 # explain a past action
autarch prove <why_id>                               # verifiable receipt (+ provenance)
autarch guarantee --forbid file.delete               # PROVE a safety invariant (CI-friendly)
autarch health --json                                # readiness probe
autarch audit export audit.jsonl                     # regulator-grade audit trail
autarch audit redact <why_id> --reason "GDPR"        # right-to-be-forgotten
autarch mesh serve --port 8787                       # sync nodes over HTTP (no broker)
```

## What you get, by pillar

| Need | Autarch |
|---|---|
| Governance | capability kernel (deny by default), policy-as-code, RBAC |
| Trust | Ed25519-signed provenance, tamper-evident hash-chained ledger |
| Safety | formal guarantees proven *before* running; budgets that refuse overspend |
| Multi-agent | delegation that only ever *narrows* authority |
| Reliability | durable, resumable runs (no double side effects); typed errors |
| Observability | structured event stream → JSON lines or OpenTelemetry |
| Compliance | audit export + redaction (RTBF) that keeps the integrity proof |
| Ecosystem | govern MCP + LangChain tools; serve capabilities as a governed MCP server |
| Deploy | self-contained; `Dockerfile` + health probe included |

## Production deployment

```bash
docker build -t autarch .
docker run -p 8787:8787 -v autarch-data:/data autarch
```

The container runs as a non-root user, persists the ledger to a volume, and uses
`autarch health` as its `HEALTHCHECK`. Set a passphrase to encrypt the signing
key at rest (see `examples/security.py`).

See [docs/MANIFESTO.md](MANIFESTO.md) for the vision and [docs/PLAN.md](PLAN.md)
for the full build log.
