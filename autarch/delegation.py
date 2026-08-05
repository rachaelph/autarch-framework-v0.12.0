"""Capability attenuation & delegation — true object-capability semantics.

A parent may hand a sub-agent a capability, but only a **strictly weaker** one:
the child can never exceed the authority it was given. This is enforced
*structurally* (by construction), not by trust — the foundation for safe
multi-agent hierarchies.

Attenuation may only narrow along three axes:
  * **name**   — a child may take an equal or *more specific* capability
                 (`file.*` → `file.write` is fine; `file.write` → `file.*` is not).
  * **scope**  — a path prefix may only shrink to itself or a subdirectory; a
                 child may add a prefix the parent lacked, never remove one.
  * **limits** — a numeric ceiling (e.g. `max_bytes`) may only stay the same or
                 get smaller; a child may add a limit, never raise or drop one.

Any request that would *widen* authority is rejected. `delegate` applies this to
a whole set, dropping (deny-by-default) anything no parent grant can cover.
"""
from __future__ import annotations

import os
from typing import List, Optional, Tuple

from .contracts import CapabilityGrant


def _name_within(parent_name: str, child_name: str) -> bool:
    """Whether `child_name` is equal to or more specific than `parent_name`."""
    if parent_name == child_name:
        return True
    if parent_name.endswith(".*"):
        prefix = parent_name[:-1]  # 'file.*' -> 'file.'
        target = child_name[:-1] if child_name.endswith(".*") else child_name
        return target.startswith(prefix)
    # A concrete parent authorizes only the identical capability.
    return False


def _path_within(parent_prefix: str, child_prefix: str) -> bool:
    """Whether `child_prefix` is the same as, or a subdirectory of, the parent."""
    p = os.path.normpath(parent_prefix)
    c = os.path.normpath(child_prefix)
    if c.startswith(".."):
        return False  # escapes upward
    if p == ".":
        return True  # parent is the whole root
    if c == p:
        return True
    return c.startswith(p + os.sep) or c.startswith(p + "/")


def _scope_within(parent_scope: dict, child_scope: dict) -> Tuple[bool, dict, str]:
    """Combine scopes; the result must be no broader than the parent's."""
    merged = dict(parent_scope)

    pp = parent_scope.get("path_prefix")
    cp = child_scope.get("path_prefix")
    if cp is not None and pp is not None:
        if not _path_within(pp, cp):
            return False, {}, f"scope path_prefix '{cp}' is outside parent '{pp}'"
        merged["path_prefix"] = cp
    elif cp is not None:  # parent had none; adding a prefix narrows
        merged["path_prefix"] = cp
    # (cp is None) -> inherit the parent's prefix unchanged

    # Any other scope key: a child may add one, or *narrow* an inherited one
    # (subset for allowlists/enums, strengthen freely for prohibitions), but may
    # never widen it. The subset semantics live in the scope algebra.
    from . import scoping

    for key, cval in child_scope.items():
        if key == "path_prefix":
            continue
        if key not in parent_scope:
            merged[key] = cval  # adding a constraint narrows
            continue
        ok, narrowed, reason = scoping.narrow_scope_value(key, parent_scope[key], cval)
        if not ok:
            return False, {}, reason
        merged[key] = narrowed

    return True, merged, ""


def _limits_within(parent_limits: dict, child_limits: dict) -> Tuple[bool, dict, str]:
    """Combine limits; numeric ceilings may only shrink, never grow."""
    merged = dict(parent_limits)
    for key, cval in child_limits.items():
        pval = parent_limits.get(key)
        if pval is None:
            merged[key] = cval  # adding a limit narrows (max_bytes, amount_max, ...)
        elif isinstance(cval, (int, float)) and isinstance(pval, (int, float)):
            if cval > pval:
                return False, {}, f"limit '{key}'={cval} exceeds parent's {pval}"
            merged[key] = cval
        elif cval != pval:
            return False, {}, f"limit '{key}' cannot be changed by a child"
    return True, merged, ""


def attenuate_grant(
    parent: CapabilityGrant,
    name: Optional[str] = None,
    scope: Optional[dict] = None,
    limits: Optional[dict] = None,
) -> CapabilityGrant:
    """Derive a child grant that is a strict subset of `parent`.

    Raises ValueError if the request would widen authority along any axis.
    """
    child_name = name or parent.name
    if not _name_within(parent.name, child_name):
        raise ValueError(
            f"capability '{child_name}' is not within parent '{parent.name}'"
        )

    ok, merged_scope, reason = _scope_within(parent.scope, scope or {})
    if not ok:
        raise ValueError(reason)

    ok, merged_limits, reason = _limits_within(parent.limits, limits or {})
    if not ok:
        raise ValueError(reason)

    return CapabilityGrant(
        name=child_name,
        scope=merged_scope,
        limits=merged_limits,
        depth=parent.depth + 1,
        delegated_from=parent.name,
    )


def attenuate_under(
    parent_grants: List[CapabilityGrant], requested: CapabilityGrant
) -> Optional[CapabilityGrant]:
    """Return `requested` attenuated under whichever parent grant can cover it."""
    for parent in parent_grants:
        try:
            return attenuate_grant(
                parent, name=requested.name, scope=requested.scope, limits=requested.limits
            )
        except ValueError:
            continue
    return None


def delegate(
    parent_grants: List[CapabilityGrant], requested: List[CapabilityGrant]
) -> Tuple[List[CapabilityGrant], List[CapabilityGrant]]:
    """Attenuate a requested set under the parent's authority.

    Returns (granted, dropped). Anything no parent grant can cover is dropped —
    deny-by-default for delegation, so a child can never gain authority a parent
    never held.
    """
    granted: List[CapabilityGrant] = []
    dropped: List[CapabilityGrant] = []
    for req in requested:
        child = attenuate_under(parent_grants, req)
        if child is None:
            dropped.append(req)
        else:
            granted.append(child)
    return granted, dropped
