"""Kernel tests — the deterministic gate is the safety-critical core."""
from autarch.contracts import Action, CapabilityGrant
from autarch.kernel import CapabilityKernel


def test_deny_by_default():
    kernel = CapabilityKernel(grants=[])
    result = kernel.authorize(Action(capability="file.write", params={"path": "a.txt"}))
    assert result.allowed is False
    assert "deny by default" in result.reason


def test_allow_when_granted():
    kernel = CapabilityKernel(grants=[CapabilityGrant("file.write", scope={"path_prefix": "."})])
    result = kernel.authorize(Action(capability="file.write", params={"path": "a.txt", "content": "hi"}))
    assert result.allowed is True
    assert result.grant is not None


def test_wildcard_grant_matches():
    kernel = CapabilityKernel(grants=[CapabilityGrant("file.*", scope={"path_prefix": "."})])
    assert kernel.authorize(Action("file.read", {"path": "a.txt"})).allowed is True
    assert kernel.authorize(Action("network.get", {"url": "x"})).allowed is False


def test_scope_blocks_absolute_path():
    kernel = CapabilityKernel(grants=[CapabilityGrant("file.write", scope={"path_prefix": "."})])
    result = kernel.authorize(Action("file.write", {"path": "/etc/passwd", "content": "x"}))
    assert result.allowed is False
    assert "escapes scope" in result.reason


def test_scope_blocks_parent_traversal():
    kernel = CapabilityKernel(grants=[CapabilityGrant("file.write", scope={"path_prefix": "."})])
    result = kernel.authorize(Action("file.write", {"path": "../secret.txt", "content": "x"}))
    assert result.allowed is False


def test_limit_blocks_oversized_content():
    kernel = CapabilityKernel(grants=[CapabilityGrant("file.write", limits={"max_bytes": 4})])
    result = kernel.authorize(Action("file.write", {"path": "a.txt", "content": "toolong"}))
    assert result.allowed is False
    assert "max_bytes" in result.reason
