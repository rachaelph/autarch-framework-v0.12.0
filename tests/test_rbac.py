"""RBAC tests — governance of *who* may wield which capabilities."""
from autarch import Agent, capability
from autarch.rbac import AccessControl, Principal, Role, RoleRegistry


def _ac():
    return AccessControl(RoleRegistry([
        Role("admin", grantable=["file.*", "payment.*"]),
        Role("analyst", grantable=["file.read"]),
        Role("operator", grantable=["file.read", "file.write"]),
    ]))


# --- role / registry ------------------------------------------------------

def test_role_permits_exact_and_wildcard():
    role = Role("r", grantable=["file.*", "payment.read"])
    assert role.permits("file.write") is True
    assert role.permits("payment.read") is True
    assert role.permits("payment.send") is False


def test_registry_lookup():
    reg = RoleRegistry([Role("admin", ["*"])])
    assert reg.get("admin").permits("anything") is True
    assert reg.get("missing") is None


# --- access control -------------------------------------------------------

def test_can_grant_across_roles():
    ac = _ac()
    multi = Principal("u", ["analyst", "operator"])
    assert ac.can_grant(multi, "file.write") is True   # via operator
    assert ac.can_grant(multi, "payment.send") is False


def test_authorize_grants_splits_allowed_denied():
    ac = _ac()
    analyst = Principal("u", ["analyst"])
    allowed, denied = ac.authorize_grants(analyst, [
        capability("file.read"), capability("file.write"), capability("payment.send"),
    ])
    assert [g.name for g in allowed] == ["file.read"]
    assert {g.name for g in denied} == {"file.write", "payment.send"}


def test_no_roles_denies_everything():
    ac = _ac()
    allowed, denied = ac.authorize_grants(Principal("u", []), [capability("file.read")])
    assert allowed == []
    assert len(denied) == 1


# --- agent integration ----------------------------------------------------

def test_agent_filters_grants_by_principal(tmp_path):
    agent = Agent(
        intent="pay an invoice", council=["mock"],
        grants=[capability("payment.send"), capability("file.read")],
        workspace=tmp_path,
        principal=Principal("u1", ["analyst"]), access=_ac(), sign=False,
    )
    assert [g.name for g in agent.grants] == ["file.read"]
    assert [g.name for g in agent.denied_grants] == ["payment.send"]


def test_admin_keeps_privileged_capability(tmp_path):
    agent = Agent(
        intent="pay an invoice", council=["mock"],
        grants=[capability("payment.send"), capability("file.read")],
        workspace=tmp_path,
        principal=Principal("admin1", ["admin"]), access=_ac(), sign=False,
    )
    assert {g.name for g in agent.grants} == {"payment.send", "file.read"}
    assert agent.denied_grants == []


def test_no_principal_means_no_rbac(tmp_path):
    # Back-compat: without principal+access, all grants pass through.
    agent = Agent(
        intent="x", council=["mock"],
        grants=[capability("payment.send")],
        workspace=tmp_path, sign=False,
    )
    assert [g.name for g in agent.grants] == ["payment.send"]
    assert agent.denied_grants == []


def test_rbac_denied_capability_is_unusable_at_runtime(tmp_path):
    # An analyst's agent cannot delete even if it tries — RBAC dropped the grant,
    # so the kernel denies the action.
    from autarch.contracts import Action

    agent = Agent(
        intent="delete things", council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path,
        principal=Principal("u", ["analyst"]), access=_ac(), sign=False,
    )
    assert agent.grants == []
    assert agent.kernel.authorize(Action("file.delete", {"path": "a.txt"})).allowed is False
