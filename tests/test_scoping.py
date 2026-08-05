"""Tests for the general capability scope algebra."""
from autarch.agent import capability
from autarch.contracts import Action
from autarch.kernel import CapabilityKernel


def _kernel(grant):
    return CapabilityKernel([grant])


def test_path_prefix_still_confines():
    k = _kernel(capability("file.write", scope={"path_prefix": "reports"}))
    assert k.authorize(Action("file.write", {"path": "reports/q.txt", "content": "x"})).allowed
    assert not k.authorize(Action("file.write", {"path": "secrets.txt", "content": "x"})).allowed


def test_max_bytes_still_limits():
    k = _kernel(capability("file.write", limits={"max_bytes": 4}))
    assert k.authorize(Action("file.write", {"path": "a", "content": "hi"})).allowed
    assert not k.authorize(Action("file.write", {"path": "a", "content": "toolong"})).allowed


def test_host_allowlist():
    k = _kernel(capability("net.fetch", scope={"host_allowlist": ["api.github.com"]}))
    assert k.authorize(Action("net.fetch", {"url": "https://api.github.com/x"})).allowed
    d = k.authorize(Action("net.fetch", {"url": "https://evil.com/x"}))
    assert not d.allowed and "not in allowlist" in d.reason


def test_port_allowlist():
    k = _kernel(capability("net.connect", scope={"port_allowlist": [443]}))
    assert k.authorize(Action("net.connect", {"host": "example.com:443"})).allowed
    assert not k.authorize(Action("net.connect", {"host": "example.com:22"})).allowed


def test_amount_ceiling():
    k = _kernel(capability("payment.send", limits={"amount_max": 100}))
    assert k.authorize(Action("payment.send", {"amount": 99})).allowed
    assert not k.authorize(Action("payment.send", {"amount": 100.01})).allowed


def test_enum_membership():
    k = _kernel(capability("deploy.env", scope={"enum": {"env": ["staging", "dev"]}}))
    assert k.authorize(Action("deploy.env", {"env": "staging"})).allowed
    assert not k.authorize(Action("deploy.env", {"env": "prod"})).allowed


def test_regex_shape():
    k = _kernel(capability("user.create", scope={"regex": {"email": r"[^@]+@corp\.com"}}))
    assert k.authorize(Action("user.create", {"email": "a@corp.com"})).allowed
    assert not k.authorize(Action("user.create", {"email": "a@evil.com"})).allowed


def test_forbid_substrings():
    k = _kernel(capability("db.query", scope={"forbid_substrings": {"sql": ["DROP", "DELETE"]}}))
    assert k.authorize(Action("db.query", {"sql": "SELECT 1"})).allowed
    assert not k.authorize(Action("db.query", {"sql": "DROP TABLE users"})).allowed


def test_forbid_data_classes():
    k = _kernel(capability("email.send", scope={"forbid_data_classes": ["PHI", "PII"]}))
    assert k.authorize(Action("email.send", {"to": "x", "data_classes": ["public"]})).allowed
    assert not k.authorize(Action("email.send", {"to": "x", "data_classes": ["PHI"]})).allowed


def test_malformed_regex_fails_closed():
    k = _kernel(capability("x.y", scope={"regex": {"p": "([unclosed"}}))
    # a bad pattern must DENY, never crash
    assert not k.authorize(Action("x.y", {"p": "anything"})).allowed


def test_unknown_scope_key_is_inert_metadata():
    k = _kernel(capability("x.y", scope={"note": "just a label"}))
    assert k.authorize(Action("x.y", {"anything": 1})).allowed
