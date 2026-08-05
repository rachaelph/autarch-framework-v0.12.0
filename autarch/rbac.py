"""Role-based access control over capabilities — governance of *who*.

The capability kernel governs *what* an agent may do. RBAC governs *who* may
wield which capabilities in the first place. A `Principal` (a user/service with
roles) can only be granted capabilities its roles permit; anything else is
dropped (deny by default), exactly mirroring how delegation narrows authority.

This composes with — it does not replace — the kernel and delegation: a request
must pass RBAC (is this principal allowed to hold this capability?) *and* the
kernel (is this specific action within the granted scope?).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .contracts import CapabilityGrant
from .policy import _capability_matches


@dataclass
class Role:
    """A named role with the set of capability patterns it may be granted."""

    name: str
    grantable: List[str] = field(default_factory=list)  # e.g. ["file.*", "payment.read"]

    def permits(self, capability_name: str) -> bool:
        return any(_capability_matches(pattern, capability_name) for pattern in self.grantable)


@dataclass
class Principal:
    """An authenticated actor (user or service) and the roles it holds."""

    id: str
    roles: List[str] = field(default_factory=list)


class RoleRegistry:
    """The catalog of roles known to a deployment."""

    def __init__(self, roles=None):
        self._roles: Dict[str, Role] = {}
        for role in (roles or []):
            self.add(role)

    def add(self, role: Role) -> None:
        self._roles[role.name] = role

    def get(self, name: str):
        return self._roles.get(name)

    def names(self) -> List[str]:
        return list(self._roles)


class AccessControl:
    """Decides which capabilities a principal is permitted to wield."""

    def __init__(self, registry: RoleRegistry):
        self.registry = registry

    def can_grant(self, principal: Principal, capability_name: str) -> bool:
        """True iff any of the principal's roles permits this capability."""
        for role_name in principal.roles:
            role = self.registry.get(role_name)
            if role is not None and role.permits(capability_name):
                return True
        return False

    def authorize_grants(
        self, principal: Principal, grants: List[CapabilityGrant]
    ) -> Tuple[List[CapabilityGrant], List[CapabilityGrant]]:
        """Split `grants` into (allowed, denied) for this principal.

        Deny-by-default: a capability no role permits is denied, so a principal can
        never wield authority beyond what its roles confer.
        """
        allowed: List[CapabilityGrant] = []
        denied: List[CapabilityGrant] = []
        for grant in grants:
            (allowed if self.can_grant(principal, grant.name) else denied).append(grant)
        return allowed, denied
