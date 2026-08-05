"""Typed errors — a stable, catchable error taxonomy for enterprise use.

A production caller needs to distinguish "the model failed" from "the kernel
denied this" from "we ran out of budget" — programmatically, not by string
matching. Every Autarch error carries a stable ``code`` and structured
``context`` so it can be logged, traced, and handled deterministically.

These are raised at **boundaries** (the public SDK surface), not sprinkled
through the happy path. Internally the deliberation loop stays exception-free and
returns governed results; these types are for callers who opt into raising.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AutarchError(Exception):
    """Base class for all Autarch errors. Catch this to catch everything."""

    code = "autarch_error"

    def __init__(self, message: str, context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.context: Dict[str, Any] = dict(context or {})

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}

    def __str__(self) -> str:
        if self.context:
            return f"[{self.code}] {self.message} ({self.context})"
        return f"[{self.code}] {self.message}"


# -- governance: an action was refused by a deterministic control --------------
class GovernanceError(AutarchError):
    """Base for refusals by the governance layer (kernel/policy/budget)."""

    code = "governance_error"


class CapabilityDenied(GovernanceError):
    """The capability kernel did not authorize the action."""

    code = "capability_denied"


class PolicyDenied(GovernanceError):
    """A policy denied (or required ratification of) the action."""

    code = "policy_denied"


class BudgetExceeded(GovernanceError):
    """The economic kernel refused the action as unaffordable."""

    code = "budget_exceeded"


class DelegationError(GovernanceError):
    """A delegation attempted to widen authority beyond the parent's grant."""

    code = "delegation_error"


class AccessDenied(GovernanceError):
    """A principal attempted to wield a capability its roles do not permit (RBAC)."""

    code = "access_denied"


# -- secrets: a key/secret could not be read or written securely ---------------
class SecretError(AutarchError):
    """A secret (e.g. an encrypted private key) could not be sealed or opened."""

    code = "secret_error"


# -- execution: something failed while acting ----------------------------------
class AdapterError(AutarchError):
    """An adapter failed to perform an action."""

    code = "adapter_error"


class ModelError(AutarchError):
    """A model provider failed or returned unusable output."""

    code = "model_error"


class ModelUnavailable(ModelError):
    """A model provider was transiently unreachable (network/5xx/timeout).

    Distinct from ``ModelError`` so the resilience layer knows it is worth a
    backoff-and-retry rather than a hard failure.
    """

    code = "model_unavailable"


class RateLimited(ModelError):
    """A model provider refused the call because a rate/quota limit was hit.

    Carries ``retry_after`` (seconds) when the provider tells us how long to wait,
    so the resilience layer can honor it instead of guessing.
    """

    code = "rate_limited"

    def __init__(self, message, retry_after=None, context=None):
        super().__init__(message, context)
        self.retry_after = retry_after
        if retry_after is not None:
            self.context.setdefault("retry_after", retry_after)


class CircuitOpen(ModelError):
    """The provider's circuit breaker is open; the call failed fast by design."""

    code = "circuit_open"


# -- input: a caller supplied something invalid at the boundary ----------------
class ValidationError(AutarchError):
    """Invalid input supplied to the public API."""

    code = "validation_error"
