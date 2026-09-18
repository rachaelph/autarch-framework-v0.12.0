"""Production agent-factory: immutable specs, approval binding, proof, and lifecycle."""
from __future__ import annotations

import pytest

from autarch import (
    AccessControl,
    AccessDenied,
    AgentBlueprint,
    AgentFactory,
    ApprovalQueue,
    DeploymentState,
    DomainPack,
    FileSystemAdapter,
    Invariant,
    Principal,
    Role,
    RoleRegistry,
    ValidationError,
    capability,
)


def _pack(*, approval=True, grants=None, invariants=None, policies=None, version="1.0.0"):
    blueprint = AgentBlueprint(
        name="reporter",
        version=version,
        directive="Create an accurate report.",
        grants=grants if grants is not None else [capability("file.write")],
        policies=policies or [],
        invariants=invariants or [Invariant.forbid("file.delete")],
        adapters=["filesystem"],
        requires_approval=approval,
    )
    return DomainPack(
        name="general-business",
        version=version,
        blueprints={"reporter": blueprint},
        adapter_catalog={"filesystem": lambda workspace: FileSystemAdapter(workspace)},
    )


def test_blueprint_is_deny_by_default_and_round_trips():
    blueprint = AgentBlueprint(name="minimal", version="1.0.0", requires_approval=False)
    assert blueprint.grants == []
    assert blueprint.validate().valid
    restored = AgentBlueprint.from_dict(blueprint.to_dict())
    assert restored.to_dict() == blueprint.to_dict()
    assert restored.fingerprint == blueprint.fingerprint


def test_fingerprint_is_stable_and_content_bound():
    first = AgentBlueprint(name="auditor", version="1.0.0", requires_approval=False)
    second = AgentBlueprint(name="auditor", version="1.0.0", requires_approval=False)
    changed = AgentBlueprint(name="auditor", version="1.0.0", grants=[capability("file.read")], requires_approval=False)
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert first.id.startswith("auditor:1.0.0@")


def test_blueprint_rejects_nonportable_and_invalid_configuration():
    blueprint = AgentBlueprint(
        name="bad name",
        version="latest",
        grants=[capability("file.read")],
        policies=[{"name": "oops", "effect": "permit", "when": lambda _: True}],
        council=[],
        approval_quorum=0,
    )
    result = blueprint.validate()
    assert not result.valid
    codes = {issue.code for issue in result.errors}
    assert {"invalid_name", "invalid_version", "invalid_policy_effect", "invalid_policy_condition", "invalid_council", "invalid_quorum"} <= codes


def test_pack_rejects_unbacked_grants(tmp_path):
    pack = _pack(grants=[capability("payment.send")])
    result = pack.validate(tmp_path)
    assert not result.valid
    assert "unbacked_grant" in {issue.code for issue in result.errors}


def test_pack_fails_closed_when_required_guarantee_does_not_hold(tmp_path):
    pack = _pack(grants=[capability("file.delete")], invariants=[Invariant.forbid("file.delete")])
    result = pack.validate(tmp_path)
    assert not result.valid
    assert "guarantee_failed" in {issue.code for issue in result.errors}


def test_compile_without_required_approval_is_denied(tmp_path):
    pack = _pack()
    with pytest.raises(AccessDenied):
        pack.compile("reporter", "create report.txt", tmp_path)


def test_approval_is_bound_to_exact_pack_and_blueprint_content(tmp_path):
    queue = ApprovalQueue(tmp_path / "approvals.db")
    factory = AgentFactory(queue)
    pack = _pack()
    factory.register(pack, tmp_path)
    approval = factory.request_approval("general-business", "reporter", requested_by="service")
    approval = queue.ratify(approval.id, by="risk-owner")

    deployment = factory.create("general-business", "reporter", "create report.txt that says ready", tmp_path, approval=approval)
    assert deployment.state == DeploymentState.COMPILED
    assert deployment.approved_by == "risk-owner"
    assert deployment.fingerprint == pack.blueprints["reporter"].fingerprint

    other = _pack(version="1.1.0")
    factory.register(other, tmp_path)
    with pytest.raises(AccessDenied):
        factory.create("general-business", "reporter", "create x.txt", tmp_path, version="1.1.0", approval=approval)


def test_quorum_must_be_satisfied(tmp_path):
    queue = ApprovalQueue(tmp_path / "approvals.db")
    pack = _pack()
    pack.blueprints["reporter"].approval_quorum = 2
    factory = AgentFactory(queue)
    factory.register(pack, tmp_path)
    pending = factory.request_approval("general-business", "reporter")
    pending = queue.ratify(pending.id, by="reviewer-one")
    assert not pending.ratified
    with pytest.raises(AccessDenied):
        factory.create("general-business", "reporter", "create x.txt", tmp_path, approval=pending)


def test_factory_uses_latest_registered_semver(tmp_path):
    factory = AgentFactory()
    factory.register(_pack(approval=False, version="1.2.0"), tmp_path)
    factory.register(_pack(approval=False, version="1.10.0"), tmp_path)
    assert factory.get("general-business").version == "1.10.0"


def test_registered_version_cannot_be_replaced_with_different_content(tmp_path):
    factory = AgentFactory()
    factory.register(_pack(approval=False), tmp_path)
    changed = _pack(approval=False)
    changed.blueprints["reporter"].description = "different immutable content"
    with pytest.raises(ValidationError):
        factory.register(changed, tmp_path)


def test_compile_applies_rbac_before_runtime(tmp_path):
    pack = _pack(approval=False, grants=[capability("file.read"), capability("file.write")])
    registry = RoleRegistry([Role("reader", ["file.read"])])
    deployment = pack.compile(
        "reporter",
        "read report.txt",
        tmp_path,
        principal=Principal("alice", ["reader"]),
        access=AccessControl(registry),
    )
    assert [grant.name for grant in deployment.agent.grants] == ["file.read"]
    assert [grant.name for grant in deployment.agent.denied_grants] == ["file.write"]


def test_deployment_runs_once_and_tracks_lifecycle(tmp_path):
    pack = _pack(approval=False)
    deployment = pack.compile("reporter", "create report.txt that says ready", tmp_path)
    result = deployment.run()
    assert result.executed
    assert deployment.state == DeploymentState.SUCCEEDED
    assert deployment.completed_at is not None
    with pytest.raises(Exception):
        deployment.run()


def test_retired_deployment_cannot_run(tmp_path):
    deployment = _pack(approval=False).compile("reporter", "create x.txt", tmp_path)
    deployment.retire()
    assert deployment.state == DeploymentState.RETIRED
    with pytest.raises(Exception):
        deployment.run()


def test_declarative_policy_is_compiled_into_agent(tmp_path):
    policies = [{
        "name": "no-delete",
        "effect": "deny",
        "capability": "file.delete",
        "reason": "domain policy",
    }]
    pack = _pack(
        approval=False,
        grants=[capability("file.delete")],
        invariants=[Invariant.forbid("file.delete")],
        policies=policies,
    )
    deployment = pack.compile("reporter", "delete x.txt", tmp_path)
    decision = deployment.agent.policy_engine.policies[0]
    assert decision.name == "no-delete"
