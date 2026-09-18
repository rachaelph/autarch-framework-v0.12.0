# Governed agent factory

Autarch's factory creates domain-specific agents from declarative, reviewable specifications. It does **not** generate or execute arbitrary Python. A blueprint requests authority; validation, human approval, static guarantees, RBAC, policy, and the capability kernel decide what is deployed and what can act.

## Architecture

```text
Business requirement
       |
Reviewed DomainPack (industry policies + trusted adapter catalog)
       |
Versioned AgentBlueprint (role + grants + budget + invariants)
       |
Validation -> static proof -> content-bound approval -> RBAC
       |
AgentDeployment -> capability kernel -> audited Agent
```

`AgentBlueprint` is deny-by-default: an empty grant list creates an agent with no authority. Policies use the JSON policy DSL, so specifications remain portable and fingerprintable. Every blueprint and domain pack has a deterministic SHA-256 fingerprint. An approval is bound to the exact pack ID, blueprint ID, and fingerprint, preventing approval reuse after a specification changes.

## Example

```python
from autarch import (
    AgentBlueprint,
    AgentFactory,
    ApprovalQueue,
    DomainPack,
    FileSystemAdapter,
    Invariant,
    capability,
)

blueprint = AgentBlueprint(
    name="report-writer",
    version="1.0.0",
    directive="Write factual reports from approved source material.",
    grants=[capability("file.write", scope={"path_prefix": "reports"})],
    adapters=["filesystem"],
    invariants=[
        Invariant.forbid("file.delete"),
        Invariant.confine("file.write", "reports"),
    ],
    budget={"calls": 10, "risk": 3},
    requires_approval=True,
    approval_quorum=2,
)

pack = DomainPack(
    name="enterprise-reporting",
    version="1.0.0",
    blueprints={blueprint.name: blueprint},
    adapter_catalog={
        "filesystem": lambda workspace: FileSystemAdapter(workspace),
    },
)

queue = ApprovalQueue("./runtime/approvals.db")
factory = AgentFactory(queue)
factory.register(pack, workspace="./runtime")

approval = factory.request_approval(
    "enterprise-reporting", "report-writer", requested_by="reporting-service"
)
approval = queue.ratify(approval.id, by="business-owner")
approval = queue.ratify(approval.id, by="risk-owner")

deployment = factory.create(
    "enterprise-reporting",
    "report-writer",
    "create reports/q3.txt that says revenue is on target",
    workspace="./runtime",
    approval=approval,
)
result = deployment.run()
```

## Domain-pack boundaries

A production domain pack should contain only:

- reviewed blueprints;
- declarative policy specifications;
- static safety invariants;
- references to trusted adapter implementations;
- budget ceilings and approval quorum;
- non-secret metadata.

Adapter code remains application-owned and must be reviewed independently. Secrets must be supplied by the deployment environment, never embedded in a blueprint or pack.

## Production controls

1. Register immutable semantic versions. The registry rejects different content under an existing version.
2. Require quorum approval for agents with side effects or regulated decisions.
3. Bind RBAC principals at compilation so unauthorized grants are removed before reaching the kernel.
4. Use unconditional policies for properties that must be statically provable.
5. Run domain evaluation suites in a sandbox before registering a pack.
6. Export structured events and retain the signed Why-memory ledger.
7. Retire deployments when a blueprint, policy, adapter, or regulation changes.

## Current boundary

This release provides the secure factory foundation: schemas, validation, fingerprints, approval binding, static proofs, RBAC-aware compilation, and deployment lifecycle enforcement. A model-backed requirement-to-blueprint proposer can be added above this API, but its output must pass the same deterministic pipeline and must never self-approve.
