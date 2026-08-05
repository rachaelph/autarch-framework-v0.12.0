"""Attenuation tests — a child grant may only narrow, never widen."""
import pytest

from autarch import capability
from autarch.delegation import attenuate_grant, attenuate_under, delegate


# --- name axis ------------------------------------------------------------

def test_wildcard_parent_to_specific_child():
    parent = capability("file.*")
    child = attenuate_grant(parent, name="file.write")
    assert child.name == "file.write"
    assert child.depth == 1
    assert child.delegated_from == "file.*"


def test_specific_child_to_wildcard_is_rejected():
    parent = capability("file.write")
    with pytest.raises(ValueError):
        attenuate_grant(parent, name="file.*")


def test_unrelated_capability_rejected():
    parent = capability("file.*")
    with pytest.raises(ValueError):
        attenuate_grant(parent, name="network.get")


def test_equal_name_allowed():
    parent = capability("file.write")
    child = attenuate_grant(parent, name="file.write")
    assert child.name == "file.write"


def test_narrower_wildcard_allowed():
    parent = capability("file.*")
    child = attenuate_grant(parent, name="file.report.*")
    assert child.name == "file.report.*"


# --- scope axis -----------------------------------------------------------

def test_scope_subdirectory_allowed():
    parent = capability("file.write", scope={"path_prefix": "data"})
    child = attenuate_grant(parent, scope={"path_prefix": "data/reports"})
    assert child.scope["path_prefix"] == "data/reports"


def test_scope_escape_rejected():
    parent = capability("file.write", scope={"path_prefix": "data"})
    with pytest.raises(ValueError):
        attenuate_grant(parent, scope={"path_prefix": "secrets"})


def test_scope_added_when_parent_had_none():
    parent = capability("file.write")  # no path_prefix
    child = attenuate_grant(parent, scope={"path_prefix": "reports"})
    assert child.scope["path_prefix"] == "reports"


def test_scope_inherited_when_child_silent():
    parent = capability("file.write", scope={"path_prefix": "data"})
    child = attenuate_grant(parent)
    assert child.scope["path_prefix"] == "data"


def test_scope_other_key_cannot_change():
    parent = capability("x", scope={"region": "eu"})
    with pytest.raises(ValueError):
        attenuate_grant(parent, scope={"region": "us"})


# --- limits axis ----------------------------------------------------------

def test_limit_can_shrink():
    parent = capability("file.write", limits={"max_bytes": 1000})
    child = attenuate_grant(parent, limits={"max_bytes": 100})
    assert child.limits["max_bytes"] == 100


def test_limit_cannot_grow():
    parent = capability("file.write", limits={"max_bytes": 1000})
    with pytest.raises(ValueError):
        attenuate_grant(parent, limits={"max_bytes": 5000})


def test_limit_added_when_parent_had_none():
    parent = capability("file.write")
    child = attenuate_grant(parent, limits={"max_bytes": 50})
    assert child.limits["max_bytes"] == 50


def test_limit_inherited_when_child_silent():
    parent = capability("file.write", limits={"max_bytes": 1000})
    child = attenuate_grant(parent)
    assert child.limits["max_bytes"] == 1000


# --- multi-axis & sets ----------------------------------------------------

def test_all_axes_narrow_together():
    parent = capability("file.*", scope={"path_prefix": "data"}, limits={"max_bytes": 1000})
    child = attenuate_grant(parent, name="file.write", scope={"path_prefix": "data/x"}, limits={"max_bytes": 10})
    assert child.name == "file.write"
    assert child.scope["path_prefix"] == "data/x"
    assert child.limits["max_bytes"] == 10


def test_attenuate_under_picks_a_covering_parent():
    parents = [capability("file.read"), capability("file.write", scope={"path_prefix": "out"})]
    got = attenuate_under(parents, capability("file.write", scope={"path_prefix": "out/x"}))
    assert got is not None
    assert got.name == "file.write"
    assert got.scope["path_prefix"] == "out/x"


def test_delegate_drops_uncovered():
    parents = [capability("file.write", scope={"path_prefix": "out"})]
    granted, dropped = delegate(parents, [
        capability("file.write", scope={"path_prefix": "out/a"}),
        capability("file.delete"),
        capability("network.get"),
    ])
    assert [g.name for g in granted] == ["file.write"]
    assert {d.name for d in dropped} == {"file.delete", "network.get"}


def test_delegation_depth_increments():
    parent = capability("file.*")
    child = attenuate_grant(parent, name="file.write")
    grandchild = attenuate_grant(child, name="file.write", scope={"path_prefix": "a"})
    assert parent.depth == 0
    assert child.depth == 1
    assert grandchild.depth == 2
