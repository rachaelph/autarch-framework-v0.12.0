"""Governed agent factory — portable specifications, not generated authority.

The factory turns reviewed, versioned domain specifications into ordinary
:class:`autarch.agent.Agent` instances.  It deliberately generates configuration,
never executable Python: models may propose a blueprint, but validation, approval,
static guarantees, RBAC, and the capability kernel decide whether it can run.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .adapters import Adapter
from .approval import Approval, ApprovalQueue
from .contracts import CapabilityGrant, new_id
from .errors import AccessDenied, GovernanceError, ValidationError
from .events import EventSink
from .guarantees import CONFINE, FORBID, REQUIRE_APPROVAL, GuaranteeReport, Invariant, prove_guarantees
from .policy import Policy, PolicyEffect
from .policydsl import compile_policies
from .rbac import AccessControl, Principal

_SCHEMA_VERSION = 1
_NAME = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{1,127}$")
_CAPABILITY = re.compile(r"^(?:\*|[a-zA-Z][a-zA-Z0-9_-]*(?:\.(?:[a-zA-Z0-9_-]+|\*))*)$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
_VALID_EFFECTS = {effect.value for effect in PolicyEffect}
_VALID_INVARIANTS = {FORBID, REQUIRE_APPROVAL, CONFINE}
AdapterSource = Union[Adapter, Callable[[Path], Adapter]]


@dataclass(frozen=True)
class ValidationIssue:
    """One machine-readable factory validation finding."""

    code: str
    message: str
    path: str = ""


@dataclass
class FactoryValidation:
    """Complete validation result; warnings never make a specification valid."""

    errors: List[ValidationIssue] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def messages(self) -> List[str]:
        return [issue.message for issue in self.errors]

    def require_valid(self) -> None:
        if not self.valid:
            raise ValidationError(
                "agent factory specification is invalid",
                context={"errors": [issue.__dict__ for issue in self.errors]},
            )


@dataclass
class AgentBlueprint:
    """A declarative, versioned, deny-by-default agent specification.

    ``policies`` are policy-DSL dictionaries rather than Python callables, making
    the complete security configuration serializable, reviewable, and hashable.
    Empty ``grants`` means no authority.
    """

    name: str
    version: str
    description: str = ""
    directive: str = ""
    grants: List[CapabilityGrant] = field(default_factory=list)
    policies: List[dict] = field(default_factory=list)
    invariants: List[Invariant] = field(default_factory=list)
    council: List[str] = field(default_factory=lambda: ["mock"])
    adapters: List[str] = field(default_factory=list)
    budget: Optional[dict] = None
    requires_approval: bool = True
    approval_quorum: int = 1
    metadata: dict = field(default_factory=dict)
    schema_version: int = _SCHEMA_VERSION

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def id(self) -> str:
        return f"{self.name}:{self.version}@{self.fingerprint[:12]}"

    def compiled_policies(self) -> List[Policy]:
        return compile_policies(self.policies)

    def static_proof(
        self,
        extra_policies: Optional[List[dict]] = None,
        extra_invariants: Optional[List[Invariant]] = None,
    ) -> GuaranteeReport:
        policies = compile_policies(list(extra_policies or []) + self.policies)
        invariants = list(extra_invariants or []) + self.invariants
        return prove_guarantees(invariants, self.grants, policies)

    def validate(self) -> FactoryValidation:
        result = FactoryValidation()
        _validate_identity(self.name, self.version, self.schema_version, result, "blueprint")
        _validate_grants(self.grants, result)
        _validate_policies(self.policies, result)
        _validate_invariants(self.invariants, result)
        if not isinstance(self.approval_quorum, int) or self.approval_quorum < 1:
            result.errors.append(ValidationIssue("invalid_quorum", "approval_quorum must be a positive integer", "approval_quorum"))
        if not self.council or not all(isinstance(item, str) and item.strip() for item in self.council):
            result.errors.append(ValidationIssue("invalid_council", "council must contain at least one provider specification", "council"))
        if len(set(self.adapters)) != len(self.adapters):
            result.errors.append(ValidationIssue("duplicate_adapter", "adapter references must be unique", "adapters"))
        if self.budget is not None:
            if not isinstance(self.budget, dict):
                result.errors.append(ValidationIssue("invalid_budget", "budget must be an object", "budget"))
            else:
                for key, value in self.budget.items():
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                        result.errors.append(ValidationIssue("invalid_budget_limit", f"budget limit '{key}' must be a non-negative number", f"budget.{key}"))
        try:
            json.dumps(self.metadata, sort_keys=True)
        except (TypeError, ValueError):
            result.errors.append(ValidationIssue("non_serializable_metadata", "metadata must be JSON-serializable", "metadata"))
        return result

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "directive": self.directive,
            "grants": [_grant_dict(grant) for grant in self.grants],
            "policies": _json_copy(self.policies),
            "invariants": [_invariant_dict(inv) for inv in self.invariants],
            "council": list(self.council),
            "adapters": list(self.adapters),
            "budget": _json_copy(self.budget),
            "requires_approval": self.requires_approval,
            "approval_quorum": self.approval_quorum,
            "metadata": _json_copy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentBlueprint":
        if not isinstance(data, dict):
            raise ValidationError("blueprint payload must be an object")
        try:
            blueprint = cls(
                schema_version=data.get("schema_version", _SCHEMA_VERSION),
                name=data["name"],
                version=data["version"],
                description=data.get("description", ""),
                directive=data.get("directive", ""),
                grants=[CapabilityGrant(**item) for item in data.get("grants", [])],
                policies=_json_copy(data.get("policies", [])),
                invariants=[Invariant(**item) for item in data.get("invariants", [])],
                council=list(data.get("council", ["mock"])),
                adapters=list(data.get("adapters", [])),
                budget=_json_copy(data.get("budget")),
                requires_approval=bool(data.get("requires_approval", True)),
                approval_quorum=data.get("approval_quorum", 1),
                metadata=_json_copy(data.get("metadata", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("malformed blueprint payload", context={"error": str(exc)}) from exc
        blueprint.validate().require_valid()
        return blueprint


@dataclass
class DomainPack:
    """A versioned industry/domain bundle of blueprints and trusted adapters."""

    name: str
    version: str
    blueprints: Dict[str, AgentBlueprint] = field(default_factory=dict)
    adapter_catalog: Dict[str, AdapterSource] = field(default_factory=dict)
    policies: List[dict] = field(default_factory=list)
    invariants: List[Invariant] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    schema_version: int = _SCHEMA_VERSION
    strict_tools: bool = True

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.manifest(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def id(self) -> str:
        return f"{self.name}:{self.version}@{self.fingerprint[:12]}"

    def manifest(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "version": self.version,
            "blueprints": {name: bp.to_dict() for name, bp in sorted(self.blueprints.items())},
            "adapter_names": sorted(self.adapter_catalog),
            "policies": _json_copy(self.policies),
            "invariants": [_invariant_dict(inv) for inv in self.invariants],
            "metadata": _json_copy(self.metadata),
            "strict_tools": self.strict_tools,
        }

    def validate(self, workspace: Union[str, Path] = ".") -> FactoryValidation:
        result = FactoryValidation()
        _validate_identity(self.name, self.version, self.schema_version, result, "domain_pack")
        _validate_policies(self.policies, result, path="policies")
        _validate_invariants(self.invariants, result, path="invariants")
        if not self.blueprints:
            result.errors.append(ValidationIssue("empty_pack", "a domain pack must contain at least one blueprint", "blueprints"))
        if len(self.adapter_catalog) != len(set(self.adapter_catalog)):
            result.errors.append(ValidationIssue("duplicate_adapter", "adapter catalog names must be unique", "adapter_catalog"))

        materialized: Dict[str, Adapter] = {}
        for name, source in self.adapter_catalog.items():
            if not _NAME.fullmatch(name):
                result.errors.append(ValidationIssue("invalid_adapter_name", f"invalid adapter name '{name}'", f"adapter_catalog.{name}"))
                continue
            try:
                materialized[name] = _materialize(source, Path(workspace))
            except Exception as exc:
                result.errors.append(ValidationIssue("invalid_adapter", f"adapter '{name}' could not be initialized: {exc}", f"adapter_catalog.{name}"))

        for key, blueprint in self.blueprints.items():
            prefix = f"blueprints.{key}"
            if key != blueprint.name:
                result.errors.append(ValidationIssue("blueprint_key_mismatch", f"blueprint key '{key}' must equal blueprint name '{blueprint.name}'", prefix))
            checked = blueprint.validate()
            result.errors.extend(_prefix_issues(checked.errors, prefix))
            result.warnings.extend(_prefix_issues(checked.warnings, prefix))
            selected = []
            for adapter_name in blueprint.adapters:
                adapter = materialized.get(adapter_name)
                if adapter is None:
                    result.errors.append(ValidationIssue("unknown_adapter", f"blueprint references unknown adapter '{adapter_name}'", f"{prefix}.adapters"))
                else:
                    selected.append(adapter)
            if self.strict_tools:
                capabilities = {cap for adapter in selected for cap in adapter.capabilities()}
                for index, grant in enumerate(blueprint.grants):
                    if not any(grant.matches(cap) for cap in capabilities):
                        result.errors.append(ValidationIssue("unbacked_grant", f"grant '{grant.name}' has no selected adapter capability", f"{prefix}.grants[{index}]"))

            try:
                report = blueprint.static_proof(self.policies, self.invariants)
                for proof in report.failures():
                    result.errors.append(ValidationIssue("guarantee_failed", proof.reason, f"{prefix}.invariants"))
            except (KeyError, TypeError, ValueError) as exc:
                result.errors.append(ValidationIssue("invalid_policy", str(exc), f"{prefix}.policies"))
        try:
            json.dumps(self.metadata, sort_keys=True)
        except (TypeError, ValueError):
            result.errors.append(ValidationIssue("non_serializable_metadata", "metadata must be JSON-serializable", "metadata"))
        return result

    def compile(
        self,
        blueprint_name: str,
        intent: str,
        workspace: Union[str, Path],
        approval: Optional[Approval] = None,
        principal: Optional[Principal] = None,
        access: Optional[AccessControl] = None,
        events: Optional[EventSink] = None,
    ) -> "AgentDeployment":
        """Validate, prove, authorize, and instantiate one governed deployment."""
        validation = self.validate(workspace)
        validation.require_valid()
        blueprint = self.blueprints.get(blueprint_name)
        if blueprint is None:
            raise ValidationError("unknown blueprint", context={"blueprint": blueprint_name, "pack": self.id})
        _require_matching_approval(self, blueprint, approval)

        adapters = [_materialize(self.adapter_catalog[name], Path(workspace)) for name in blueprint.adapters]
        policies = compile_policies(self.policies + blueprint.policies)
        run_intent = f"{blueprint.directive.strip()}\n\n{intent.strip()}" if blueprint.directive.strip() else intent.strip()

        from .agent import Agent

        agent = Agent(
            intent=run_intent,
            council=blueprint.council,
            grants=list(blueprint.grants),
            workspace=workspace,
            adapters=adapters,
            policies=policies,
            budget=dict(blueprint.budget) if blueprint.budget is not None else None,
            principal=principal,
            access=access,
            events=events,
        )
        # Proof was conservative over pre-RBAC authority; nevertheless prove the
        # actual compiled authority as a final fail-closed boundary.
        report = prove_guarantees(self.invariants + blueprint.invariants, agent.grants, policies)
        if not report.all_hold:
            raise GovernanceError(
                "compiled agent failed required guarantees",
                context={"failures": [proof.reason for proof in report.failures()]},
            )
        return AgentDeployment(
            agent=agent,
            pack_id=self.id,
            blueprint_id=blueprint.id,
            fingerprint=blueprint.fingerprint,
            approved_by=approval.decided_by if approval is not None else "",
        )


class DeploymentState(str, Enum):
    COMPILED = "compiled"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETIRED = "retired"


@dataclass
class AgentDeployment:
    """A lifecycle-managed instance produced by the factory."""

    agent: Any
    pack_id: str
    blueprint_id: str
    fingerprint: str
    approved_by: str = ""
    id: str = field(default_factory=lambda: new_id("deploy"))
    state: DeploymentState = DeploymentState.COMPILED
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    last_error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def run(self, evaluate=None):
        with self._lock:
            if self.state != DeploymentState.COMPILED:
                raise GovernanceError("deployment is not runnable", context={"deployment": self.id, "state": self.state.value})
            self.state = DeploymentState.RUNNING
        try:
            result = self.agent.run(evaluate=evaluate)
        except Exception as exc:
            with self._lock:
                self.state = DeploymentState.FAILED
                self.last_error = str(exc)
                self.completed_at = time.time()
            raise
        with self._lock:
            self.state = DeploymentState.SUCCEEDED
            self.completed_at = time.time()
        return result

    def retire(self) -> None:
        with self._lock:
            if self.state == DeploymentState.RUNNING:
                raise GovernanceError("cannot retire a running deployment", context={"deployment": self.id})
            self.state = DeploymentState.RETIRED


class AgentFactory:
    """In-process registry and approval-aware compiler for domain packs."""

    def __init__(self, approval_queue: Optional[ApprovalQueue] = None):
        self.approval_queue = approval_queue
        self._packs: Dict[Tuple[str, str], DomainPack] = {}

    def register(self, pack: DomainPack, workspace: Union[str, Path] = ".") -> str:
        pack.validate(workspace).require_valid()
        key = (pack.name, pack.version)
        existing = self._packs.get(key)
        if existing is not None and existing.fingerprint != pack.fingerprint:
            raise ValidationError("cannot replace a registered domain-pack version with different content", context={"name": pack.name, "version": pack.version})
        self._packs[key] = pack
        return pack.id

    def get(self, name: str, version: Optional[str] = None) -> DomainPack:
        if version is not None:
            pack = self._packs.get((name, version))
        else:
            candidates = [pack for (pack_name, _), pack in self._packs.items() if pack_name == name]
            pack = max(candidates, key=lambda item: _semver_key(item.version), default=None)
        if pack is None:
            raise ValidationError("domain pack is not registered", context={"name": name, "version": version})
        return pack

    def request_approval(self, pack_name: str, blueprint_name: str, version: Optional[str] = None, requested_by: str = "") -> Approval:
        if self.approval_queue is None:
            raise ValidationError("the factory has no approval queue")
        pack = self.get(pack_name, version)
        blueprint = pack.blueprints.get(blueprint_name)
        if blueprint is None:
            raise ValidationError("unknown blueprint", context={"blueprint": blueprint_name, "pack": pack.id})
        return self.approval_queue.request(
            intent_text=f"compile agent blueprint {blueprint.id}",
            capability="factory.compile",
            params={"pack_id": pack.id, "blueprint_id": blueprint.id, "fingerprint": blueprint.fingerprint},
            rationale="production agent creation requires a reviewed, content-bound approval",
            requested_by=requested_by,
            quorum=blueprint.approval_quorum,
        )

    def create(
        self,
        pack_name: str,
        blueprint_name: str,
        intent: str,
        workspace: Union[str, Path],
        version: Optional[str] = None,
        approval: Optional[Approval] = None,
        principal: Optional[Principal] = None,
        access: Optional[AccessControl] = None,
        events: Optional[EventSink] = None,
    ) -> AgentDeployment:
        pack = self.get(pack_name, version)
        return pack.compile(blueprint_name, intent, workspace, approval, principal, access, events)

    def list_packs(self) -> List[str]:
        return [self._packs[key].id for key in sorted(self._packs)]


def _require_matching_approval(pack: DomainPack, blueprint: AgentBlueprint, approval: Optional[Approval]) -> None:
    if not blueprint.requires_approval:
        return
    expected = {"pack_id": pack.id, "blueprint_id": blueprint.id, "fingerprint": blueprint.fingerprint}
    if approval is None or not approval.ratified:
        raise AccessDenied("agent creation requires ratified approval", context=expected)
    if approval.capability != "factory.compile" or any(approval.params.get(key) != value for key, value in expected.items()):
        raise AccessDenied("approval does not authorize this immutable blueprint", context=expected)


def _validate_identity(name: Any, version: Any, schema_version: Any, result: FactoryValidation, path: str) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        result.errors.append(ValidationIssue("invalid_name", "name must be 2-128 safe identifier characters", f"{path}.name"))
    if not isinstance(version, str) or not _SEMVER.fullmatch(version):
        result.errors.append(ValidationIssue("invalid_version", "version must be semantic versioning (for example 1.2.0)", f"{path}.version"))
    if schema_version != _SCHEMA_VERSION:
        result.errors.append(ValidationIssue("unsupported_schema", f"unsupported schema version '{schema_version}'", f"{path}.schema_version"))


def _validate_grants(grants: Any, result: FactoryValidation) -> None:
    if not isinstance(grants, list):
        result.errors.append(ValidationIssue("invalid_grants", "grants must be a list", "grants"))
        return
    seen = set()
    for index, grant in enumerate(grants):
        if not isinstance(grant, CapabilityGrant) or not _CAPABILITY.fullmatch(grant.name):
            result.errors.append(ValidationIssue("invalid_grant", "grant must have a valid capability name", f"grants[{index}]"))
            continue
        key = json.dumps(_grant_dict(grant), sort_keys=True)
        if key in seen:
            result.warnings.append(ValidationIssue("duplicate_grant", f"duplicate grant '{grant.name}'", f"grants[{index}]"))
        seen.add(key)
        if grant.depth != 0 or grant.delegated_from:
            result.errors.append(ValidationIssue("delegated_blueprint_grant", "blueprints must contain root grants, not delegated runtime grants", f"grants[{index}]"))


def _validate_policies(policies: Any, result: FactoryValidation, path: str = "policies") -> None:
    if not isinstance(policies, list):
        result.errors.append(ValidationIssue("invalid_policies", "policies must be a list", path))
        return
    names = set()
    for index, spec in enumerate(policies):
        item_path = f"{path}[{index}]"
        if not isinstance(spec, dict):
            result.errors.append(ValidationIssue("invalid_policy", "policy must be a declarative object", item_path))
            continue
        if not isinstance(spec.get("name"), str) or not _NAME.fullmatch(spec["name"]):
            result.errors.append(ValidationIssue("invalid_policy_name", "policy requires a safe name", f"{item_path}.name"))
        elif spec["name"] in names:
            result.errors.append(ValidationIssue("duplicate_policy", f"duplicate policy name '{spec['name']}'", f"{item_path}.name"))
        else:
            names.add(spec["name"])
        if spec.get("effect") not in _VALID_EFFECTS:
            result.errors.append(ValidationIssue("invalid_policy_effect", f"effect must be one of {sorted(_VALID_EFFECTS)}", f"{item_path}.effect"))
        capability = spec.get("capability", "*")
        if not isinstance(capability, str) or not _CAPABILITY.fullmatch(capability):
            result.errors.append(ValidationIssue("invalid_policy_capability", "policy capability pattern is invalid", f"{item_path}.capability"))
        try:
            json.dumps(spec, sort_keys=True)
            compile_policies([spec])
        except (KeyError, TypeError, ValueError) as exc:
            result.errors.append(ValidationIssue("invalid_policy_condition", str(exc), item_path))


def _validate_invariants(invariants: Any, result: FactoryValidation, path: str = "invariants") -> None:
    if not isinstance(invariants, list):
        result.errors.append(ValidationIssue("invalid_invariants", "invariants must be a list", path))
        return
    for index, invariant in enumerate(invariants):
        item_path = f"{path}[{index}]"
        if not isinstance(invariant, Invariant) or invariant.kind not in _VALID_INVARIANTS:
            result.errors.append(ValidationIssue("invalid_invariant", "unsupported invariant", item_path))
            continue
        if not _CAPABILITY.fullmatch(invariant.capability):
            result.errors.append(ValidationIssue("invalid_invariant_capability", "invariant capability is invalid", f"{item_path}.capability"))
        if invariant.kind == CONFINE and not invariant.path_prefix:
            result.errors.append(ValidationIssue("missing_confinement", "confine invariant requires path_prefix", f"{item_path}.path_prefix"))


def _materialize(source: AdapterSource, workspace: Path) -> Adapter:
    adapter = source(workspace) if callable(source) and not isinstance(source, Adapter) else source
    if not isinstance(adapter, Adapter):
        raise TypeError("catalog entry must be an Adapter or workspace-to-Adapter factory")
    capabilities = adapter.capabilities()
    if not isinstance(capabilities, list) or not all(isinstance(cap, str) and _CAPABILITY.fullmatch(cap) for cap in capabilities):
        raise TypeError("adapter capabilities must be valid capability-name strings")
    return adapter


def _grant_dict(grant: CapabilityGrant) -> dict:
    return {
        "name": grant.name,
        "scope": _json_copy(grant.scope),
        "limits": _json_copy(grant.limits),
        "depth": grant.depth,
        "delegated_from": grant.delegated_from,
    }


def _invariant_dict(invariant: Invariant) -> dict:
    return {
        "kind": invariant.kind,
        "capability": invariant.capability,
        "path_prefix": invariant.path_prefix,
        "description": invariant.description,
    }


def _json_copy(value):
    if value is None:
        return None
    return json.loads(json.dumps(value, sort_keys=True))


def _prefix_issues(issues: List[ValidationIssue], prefix: str) -> List[ValidationIssue]:
    return [ValidationIssue(issue.code, issue.message, f"{prefix}.{issue.path}" if issue.path else prefix) for issue in issues]


def _semver_key(version: str) -> Tuple[int, int, int, str]:
    match = _SEMVER.fullmatch(version)
    if match is None:
        return (0, 0, 0, version)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), version)
