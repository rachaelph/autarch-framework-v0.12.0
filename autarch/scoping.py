"""Scope algebra — a general, typed constraint system for the capability kernel.

The original kernel understood exactly two scope primitives: ``path_prefix`` (file
confinement) and ``max_bytes`` (a write ceiling). That is too coarse for a real
governance kernel: network egress, spend, data classes, rate, enumerations, and
string shape all need to be *scopeable*, not all-or-nothing.

This module keeps the kernel **pure and deterministic** by defining scope as a set
of *stateless structural predicates* over an action's params. (Stateful concerns —
rate over time, cumulative spend — belong to the economic kernel, which already
meters them; keeping them out of here is what preserves determinism and sound
static guarantees.)

Design goals:
  * **Backward compatible.** ``path_prefix`` / ``max_bytes`` keep working exactly.
  * **Deny-by-default.** A recognized constraint that a param violates -> denied.
    An *unrecognized* scope key is treated conservatively as a match-nothing guard
    only when it looks like a constraint; plain metadata is ignored (see below).
  * **Attenuation-aware.** Every constraint knows how to *narrow*, so delegation
    can verify a child scope is a subset of its parent (see ``delegation.py``).
  * **Analyzable.** Constraints are declarative data, so the guarantee prover can
    reason about them statically.

Recognized SCOPE keys (in ``grant.scope``):
  path_prefix            str        confine file 'path'/'dest' to a subtree
  host_allowlist         [str]      allow only these hosts in 'host'/'url'
  port_allowlist         [int]      allow only these ports in 'port'/'url'
  enum                   {p:[v]}    param p must be one of the listed values
  regex                  {p:pat}    param p must fullmatch the pattern
  forbid_substrings      {p:[s]}    param p must NOT contain any listed substring
  forbid_data_classes    [str]      action's 'data_classes' must avoid these tags

Recognized LIMIT keys (in ``grant.limits`` — numeric ceilings, shrink-only):
  max_bytes              int        len(utf8('content')) <= n
  amount_max             number     'amount'/'value' <= n
  count_max              number     'count'/'quantity' <= n

Anything else in scope/limits is passed through untouched (metadata), so existing
callers and future keys never crash the kernel.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# --- keys this module actively enforces (everything else is inert metadata) ---
_SCOPE_KEYS = {
    "path_prefix",
    "host_allowlist",
    "port_allowlist",
    "enum",
    "regex",
    "forbid_substrings",
    "forbid_data_classes",
}
_LIMIT_KEYS = {"max_bytes", "amount_max", "count_max"}

# params the various constraints look at
_PATH_PARAMS = ("path", "dest")
_HOST_PARAMS = ("host", "url", "endpoint")
_AMOUNT_PARAMS = ("amount", "value")
_COUNT_PARAMS = ("count", "quantity", "n")


def recognized_scope_keys() -> set:
    return set(_SCOPE_KEYS)


def recognized_limit_keys() -> set:
    return set(_LIMIT_KEYS)


def _host_and_port(value: str) -> Tuple[Optional[str], Optional[int]]:
    """Best-effort (host, port) from a bare host, host:port, or a URL."""
    text = str(value)
    if "://" in text:
        parsed = urlparse(text)
        return (parsed.hostname, parsed.port)
    if ":" in text and text.count(":") == 1:
        host, _, port = text.partition(":")
        try:
            return host or None, int(port)
        except ValueError:
            return text, None
    return text, None


def _check_path_prefix(prefix: str, params: dict) -> Tuple[bool, str]:
    base = os.path.normpath(prefix)
    for key in _PATH_PARAMS:
        value = params.get(key)
        if value is None:
            continue
        norm = os.path.normpath(str(value))
        if os.path.isabs(norm) or norm.startswith(".."):
            return False, f"path '{value}' escapes scope '{prefix}'"
        if base not in (".", ""):
            within = (
                norm == base
                or norm.startswith(base + os.sep)
                or norm.startswith(base + "/")
            )
            if not within:
                return False, f"path '{value}' is outside scope '{prefix}'"
    return True, ""


def _check_host_allowlist(allowed: List[str], params: dict) -> Tuple[bool, str]:
    allowed_set = {str(h).lower() for h in allowed}
    for key in _HOST_PARAMS:
        value = params.get(key)
        if value is None:
            continue
        host, _ = _host_and_port(value)
        if host is None:
            return False, f"could not parse a host from '{value}'"
        if host.lower() not in allowed_set:
            return False, f"host '{host}' not in allowlist {sorted(allowed_set)}"
    return True, ""


def _check_port_allowlist(allowed: List[int], params: dict) -> Tuple[bool, str]:
    allowed_set = {int(p) for p in allowed}
    for key in _HOST_PARAMS:
        value = params.get(key)
        if value is None:
            continue
        _, port = _host_and_port(value)
        if port is None and key == "port":
            try:
                port = int(value)
            except (TypeError, ValueError):
                port = None
        if port is not None and port not in allowed_set:
            return False, f"port {port} not in allowlist {sorted(allowed_set)}"
    # explicit 'port' param too
    if "port" in params:
        try:
            p = int(params["port"])
            if p not in allowed_set:
                return False, f"port {p} not in allowlist {sorted(allowed_set)}"
        except (TypeError, ValueError):
            pass
    return True, ""


def _check_enum(spec: Dict[str, List[Any]], params: dict) -> Tuple[bool, str]:
    for param, allowed in spec.items():
        if param not in params:
            continue
        if params[param] not in allowed:
            return False, f"'{param}'={params[param]!r} not one of {allowed}"
    return True, ""


def _check_regex(spec: Dict[str, str], params: dict) -> Tuple[bool, str]:
    for param, pattern in spec.items():
        value = params.get(param)
        if value is None:
            continue
        try:
            if re.fullmatch(pattern, str(value)) is None:
                return False, f"'{param}'={value!r} does not match /{pattern}/"
        except re.error:
            # A malformed pattern must fail closed (deny), not crash.
            return False, f"invalid regex for '{param}'"
    return True, ""


def _check_forbid_substrings(spec: Dict[str, List[str]], params: dict) -> Tuple[bool, str]:
    for param, needles in spec.items():
        value = params.get(param)
        if value is None:
            continue
        text = str(value)
        for needle in needles:
            if str(needle) in text:
                return False, f"'{param}' contains forbidden substring {needle!r}"
    return True, ""


def _check_forbid_data_classes(forbidden: List[str], params: dict) -> Tuple[bool, str]:
    tags = params.get("data_classes") or params.get("data_class")
    if tags is None:
        return True, ""
    if isinstance(tags, str):
        tags = [tags]
    forbidden_set = {str(f).lower() for f in forbidden}
    hit = {str(t).lower() for t in tags} & forbidden_set
    if hit:
        return False, f"data classes {sorted(hit)} are forbidden by scope"
    return True, ""


def _check_numeric_max(keys: Tuple[str, ...], ceiling: float, params: dict) -> Tuple[bool, str]:
    for key in keys:
        if key not in params:
            continue
        try:
            amount = float(params[key])
        except (TypeError, ValueError):
            continue
        if amount > ceiling:
            return False, f"'{key}'={amount:g} exceeds ceiling {ceiling:g}"
    return True, ""


def evaluate(scope: dict, limits: dict, params: dict) -> Tuple[bool, str]:
    """Return (ok, reason) for an action's params against a grant's scope+limits.

    All recognized constraints must pass (logical AND). Unrecognized keys are
    ignored as metadata. This is the single function the kernel calls.
    """
    # --- scope constraints ---
    if "path_prefix" in scope and scope["path_prefix"] is not None:
        ok, why = _check_path_prefix(scope["path_prefix"], params)
        if not ok:
            return False, why
    if scope.get("host_allowlist"):
        ok, why = _check_host_allowlist(scope["host_allowlist"], params)
        if not ok:
            return False, why
    if scope.get("port_allowlist"):
        ok, why = _check_port_allowlist(scope["port_allowlist"], params)
        if not ok:
            return False, why
    if scope.get("enum"):
        ok, why = _check_enum(scope["enum"], params)
        if not ok:
            return False, why
    if scope.get("regex"):
        ok, why = _check_regex(scope["regex"], params)
        if not ok:
            return False, why
    if scope.get("forbid_substrings"):
        ok, why = _check_forbid_substrings(scope["forbid_substrings"], params)
        if not ok:
            return False, why
    if scope.get("forbid_data_classes"):
        ok, why = _check_forbid_data_classes(scope["forbid_data_classes"], params)
        if not ok:
            return False, why

    # --- limit constraints (numeric ceilings) ---
    max_bytes = limits.get("max_bytes")
    if max_bytes is not None:
        content = params.get("content", "")
        if isinstance(content, str) and len(content.encode("utf-8")) > max_bytes:
            return False, f"content exceeds max_bytes={max_bytes}"
    if "amount_max" in limits and limits["amount_max"] is not None:
        ok, why = _check_numeric_max(_AMOUNT_PARAMS, float(limits["amount_max"]), params)
        if not ok:
            return False, why
    if "count_max" in limits and limits["count_max"] is not None:
        ok, why = _check_numeric_max(_COUNT_PARAMS, float(limits["count_max"]), params)
        if not ok:
            return False, why

    return True, ""


def describe(scope: dict, limits: dict) -> List[str]:
    """Human-readable one-liners for each active constraint (for `prove`/UI)."""
    out: List[str] = []
    if scope.get("path_prefix") not in (None, ""):
        out.append(f"paths confined to '{scope['path_prefix']}'")
    if scope.get("host_allowlist"):
        out.append(f"hosts limited to {list(scope['host_allowlist'])}")
    if scope.get("port_allowlist"):
        out.append(f"ports limited to {list(scope['port_allowlist'])}")
    for param, allowed in (scope.get("enum") or {}).items():
        out.append(f"'{param}' in {allowed}")
    for param, pat in (scope.get("regex") or {}).items():
        out.append(f"'{param}' matches /{pat}/")
    for param, subs in (scope.get("forbid_substrings") or {}).items():
        out.append(f"'{param}' forbids {subs}")
    if scope.get("forbid_data_classes"):
        out.append(f"data classes forbidden: {list(scope['forbid_data_classes'])}")
    if limits.get("max_bytes") is not None:
        out.append(f"content <= {limits['max_bytes']} bytes")
    if limits.get("amount_max") is not None:
        out.append(f"amount <= {limits['amount_max']}")
    if limits.get("count_max") is not None:
        out.append(f"count <= {limits['count_max']}")
    return out


# --- attenuation helpers (used by delegation.py) -------------------------------

def allowlist_subset(parent: List[Any], child: List[Any]) -> bool:
    """A child allowlist narrows iff it is a subset of the parent's."""
    return set(map(_normal, child)).issubset(set(map(_normal, parent)))


def _normal(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


def narrow_scope_value(key: str, parent_val: Any, child_val: Any) -> Tuple[bool, Any, str]:
    """Combine one scope key across parent/child, enforcing subset narrowing.

    Returns (ok, narrowed_value, reason). Called by delegation for keys other than
    ``path_prefix`` (which has its own subtree logic).
    """
    if key in ("host_allowlist", "port_allowlist"):
        if not allowlist_subset(parent_val, child_val):
            return False, None, f"scope '{key}' widens beyond parent"
        return True, list(child_val), ""
    if key in ("enum",):
        # Each param's allowed set may only shrink.
        merged = dict(parent_val)
        for param, allowed in child_val.items():
            if param in parent_val and not set(allowed).issubset(set(parent_val[param])):
                return False, None, f"enum '{param}' widens beyond parent"
            merged[param] = list(allowed)
        return True, merged, ""
    if key in ("forbid_substrings", "forbid_data_classes", "regex"):
        # Adding *more* prohibition only narrows; a child may strengthen freely.
        return True, child_val, ""
    # Unknown key: conservative — must match the parent exactly.
    if parent_val != child_val:
        return False, None, f"scope '{key}' cannot be changed by a child"
    return True, child_val, ""
