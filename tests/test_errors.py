"""Typed error taxonomy tests."""
from autarch import (
    BudgetExceeded,
    CapabilityDenied,
    GovernanceError,
    AutarchError,
    ValidationError,
)


def test_all_inherit_autarch_error():
    for exc in (GovernanceError, CapabilityDenied, BudgetExceeded, ValidationError):
        assert issubclass(exc, AutarchError)


def test_governance_subclasses():
    assert issubclass(CapabilityDenied, GovernanceError)
    assert issubclass(BudgetExceeded, GovernanceError)


def test_codes_are_stable():
    assert AutarchError("x").code == "autarch_error"
    assert CapabilityDenied("x").code == "capability_denied"
    assert BudgetExceeded("x").code == "budget_exceeded"
    assert ValidationError("x").code == "validation_error"


def test_context_and_to_dict():
    err = CapabilityDenied("not allowed", context={"capability": "file.delete"})
    d = err.to_dict()
    assert d["code"] == "capability_denied"
    assert d["context"]["capability"] == "file.delete"
    assert "capability" in str(err)


def test_catchable_as_base():
    try:
        raise BudgetExceeded("over", context={"key": "cost"})
    except AutarchError as exc:
        assert exc.context["key"] == "cost"
