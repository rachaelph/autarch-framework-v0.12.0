"""Governed LangChain task suggestions for CapEx Demo V2."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Literal, Mapping, Optional, Protocol, Sequence, Set

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..errors import ModelError
from ..intelligence.azure_openai import AzureOpenAIProvider
from ..intelligence.base import ModelProvider
from .demo_v2_contracts import (
    TaskDetermination,
    TaskMatchMethod,
    TaskResolutionStatus,
)
from .demo_v2_normalization import NormalizedInvoiceLine, NormalizedInvoicePackage
from .demo_v2_reference import (
    TaskMasterSnapshot,
    TaskReferenceEntry,
    canonical_sha256,
    rank_task_candidates,
)


DEFAULT_TASK_PROMPT_VERSION = "demo-v2-task-v1"
DEFAULT_MODEL_DEPLOYMENT = "gpt-5.1"
DEFAULT_CANDIDATE_LIMIT = 10
_SHARED_CANDIDATE_LIMIT = 3
_ANCILLARY_REVIEW_DESCRIPTIONS = {
    "freight",
    "freight charge",
    "shipping",
    "shipping charge",
}

_SYSTEM_PROMPT = """\
You determine governed TaskMaster task codes for invoice lines.
Select only a taskCode listed in that line's candidates. Never create, alter,
or infer a code outside the supplied candidates. Return exactly one result for
every requested lineId. Use REVIEW with a null taskCode when the evidence is
insufficient. This stage determines a task only; it must not decide CapEx,
OpEx, capitalization thresholds, or bundling. Evidence references must be
copied only from each line's allowedEvidenceRefs.
When multiple candidates have indistinguishable descriptions and categories
and the request has no fact that distinguishes them, return REVIEW instead of
choosing a code arbitrarily.
Use the complete invoice context, but do not expand an abbreviation based only
on words found in a candidate name.
"""
_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _ModelContract(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        populate_by_name=True,
    )


class ModelTaskSuggestion(_ModelContract):
    """Structured model result for one unresolved invoice line."""

    line_id: str = Field(min_length=1)
    resolution_status: Literal["RESOLVED", "REVIEW"]
    task_code: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    item_identified: Optional[str] = None
    rationale: str = Field(min_length=1)
    evidence_refs: List[str] = Field(min_length=1)
    unresolved_reason: Optional[str] = None

    @model_validator(mode="after")
    def validate_resolution(self) -> "ModelTaskSuggestion":
        if self.resolution_status == "RESOLVED":
            if self.task_code is None:
                raise ValueError("RESOLVED result requires taskCode")
            if self.unresolved_reason is not None:
                raise ValueError(
                    "RESOLVED result cannot include unresolvedReason"
                )
        else:
            if self.task_code is not None:
                raise ValueError("REVIEW result requires null taskCode")
            if not self.unresolved_reason:
                raise ValueError("REVIEW result requires unresolvedReason")
        return self


class ModelTaskSuggestionBatch(_ModelContract):
    """Structured response for one invoice-level model call."""

    lines: List[ModelTaskSuggestion]


class TaskSuggestionResponseError(ModelError):
    """The model returned an unusable governed task response."""


class TaskSuggestionConfigurationError(ValueError):
    """The task stage is missing a model required for unresolved lines."""


class TaskSuggestionModel(Protocol):
    """Minimal invoice-batch model boundary used by the governed stage."""

    def suggest(
        self,
        request: Mapping[str, object],
    ) -> ModelTaskSuggestionBatch:
        """Return one structured response for the supplied invoice."""


class AutarchLangChainChatModel(BaseChatModel):
    """LangChain chat adapter over the Autarch model-provider seam."""

    provider: ModelProvider

    @property
    def _llm_type(self) -> str:
        return "autarch-model-provider"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        system_parts: List[str] = []
        prompt_parts: List[str] = []
        for message in messages:
            if not isinstance(message.content, str):
                raise TypeError(
                    "CapEx task suggestions require text-only messages"
                )
            if isinstance(message, SystemMessage):
                system_parts.append(message.content)
            else:
                prompt_parts.append(message.content)
        content = self.provider.complete(
            "\n\n".join(prompt_parts),
            system="\n\n".join(system_parts) or None,
        )
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=content))
            ]
        )


class LangChainTaskSuggestionModel:
    """Pydantic-structured LangChain pipeline over an Autarch provider."""

    def __init__(self, provider: ModelProvider):
        parser = PydanticOutputParser(
            pydantic_object=ModelTaskSuggestionBatch
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _SYSTEM_PROMPT),
                (
                    "human",
                    "Invoice request JSON:\n{request_json}\n\n"
                    "Return JSON matching this contract:\n"
                    "{format_instructions}",
                ),
            ]
        )
        self._chain = (
            prompt
            | AutarchLangChainChatModel(provider=provider)
            | parser
        )
        self._format_instructions = parser.get_format_instructions()

    def suggest(
        self,
        request: Mapping[str, object],
    ) -> ModelTaskSuggestionBatch:
        try:
            response = self._chain.invoke(
                {
                    "request_json": json.dumps(
                        request,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "format_instructions": self._format_instructions,
                }
            )
        except OutputParserException as exc:
            raise TaskSuggestionResponseError(
                "GPT-5.1 returned invalid task-suggestion JSON.",
                context={"stage": "task_suggestion"},
            ) from exc
        if not isinstance(response, ModelTaskSuggestionBatch):
            raise TaskSuggestionResponseError(
                "LangChain returned an unexpected task-suggestion type.",
                context={"type": type(response).__name__},
            )
        return response


def build_azure_task_suggestion_model(
    *,
    deployment: str = DEFAULT_MODEL_DEPLOYMENT,
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    api_version: Optional[str] = None,
    managed_identity_client_id: Optional[str] = None,
) -> LangChainTaskSuggestionModel:
    """Build the production LangChain pipeline over Autarch Azure OpenAI."""
    provider = AzureOpenAIProvider(
        deployment=deployment,
        endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
        json_mode=True,
        managed_identity_client_id=managed_identity_client_id,
    )
    return LangChainTaskSuggestionModel(provider)


def _normalized_text(value: str) -> str:
    return _SPACE.sub(
        " ",
        _NON_WORD.sub(" ", value.strip().lower()),
    ).strip()


def _task_snapshot_sha256(snapshot: TaskMasterSnapshot) -> str:
    return canonical_sha256(
        snapshot.model_dump(mode="json", by_alias=True)
    )


def _exact_task(
    line: NormalizedInvoiceLine,
    tasks_by_code: Mapping[str, TaskReferenceEntry],
    tasks_by_description: Mapping[str, Sequence[TaskReferenceEntry]],
) -> Optional[TaskReferenceEntry]:
    if line.source_task_code:
        return tasks_by_code.get(line.source_task_code.strip())
    description_matches = tasks_by_description.get(
        _normalized_text(line.description),
        (),
    )
    if len(description_matches) == 1:
        return description_matches[0]
    return None


def _candidate_payload(
    line: NormalizedInvoiceLine,
    candidates: Sequence[TaskReferenceEntry],
) -> Dict[str, object]:
    line_ref = f"line:{line.line_id}"
    task_refs = [f"task:{task.task_code}" for task in candidates]
    return {
        "lineId": line.line_id,
        "description": line.description,
        "partNumber": line.part_number,
        "lineAmount": line.line_amount,
        "currency": line.currency,
        "allowedEvidenceRefs": [line_ref] + task_refs,
        "candidates": [
            {
                "taskCode": task.task_code,
                "description": task.description,
                "assetCategoryMajor": task.asset_category_major,
                "assetCategoryMinor": task.asset_category_minor,
                "propertyType": task.property_type,
            }
            for task in candidates
        ],
    }


def _merge_candidates(
    primary: Sequence[TaskReferenceEntry],
    supplemental: Sequence[TaskReferenceEntry],
    *,
    limit: int,
) -> List[TaskReferenceEntry]:
    merged: List[TaskReferenceEntry] = []
    seen: Set[str] = set()
    for task in list(primary) + list(supplemental):
        if task.task_code in seen:
            continue
        merged.append(task)
        seen.add(task.task_code)
        if len(merged) == limit:
            break
    return merged


def _determination(
    *,
    package: NormalizedInvoicePackage,
    line: NormalizedInvoiceLine,
    workflow_run_id: str,
    prompt_version: str,
    snapshot: TaskMasterSnapshot,
    snapshot_sha256: str,
    created_at_utc: datetime,
    status: TaskResolutionStatus,
    match_method: TaskMatchMethod,
    confidence: float,
    rationale: str,
    evidence_refs: List[str],
    task: Optional[TaskReferenceEntry] = None,
    item_identified: Optional[str] = None,
    unresolved_reason: Optional[str] = None,
    model_deployment: Optional[str] = None,
) -> TaskDetermination:
    return TaskDetermination(
        workflow_run_id=workflow_run_id,
        package_id=package.package_id,
        invoice_id=package.invoice_id,
        line_id=line.line_id,
        resolution_status=status,
        suggested_task_code=task.task_code if task else None,
        item_identified=item_identified,
        match_method=match_method,
        confidence=confidence,
        unresolved_reason=unresolved_reason,
        rationale=rationale,
        evidence_refs=evidence_refs,
        task_master_version=snapshot.source.source_version,
        task_master_sha256=snapshot_sha256,
        model_deployment=model_deployment,
        prompt_version=prompt_version,
        created_at_utc=created_at_utc,
    )


def _validate_model_response(
    response: ModelTaskSuggestionBatch,
    candidate_sets: Mapping[str, Sequence[TaskReferenceEntry]],
    governed_codes: Set[str],
) -> Dict[str, ModelTaskSuggestion]:
    requested_line_ids = set(candidate_sets)
    response_line_ids = [line.line_id for line in response.lines]
    duplicate_ids = sorted(
        {
            line_id
            for line_id in response_line_ids
            if response_line_ids.count(line_id) > 1
        }
    )
    if duplicate_ids:
        raise TaskSuggestionResponseError(
            "GPT-5.1 returned duplicate invoice line results.",
            context={"duplicate_line_ids": duplicate_ids},
        )

    actual_line_ids = set(response_line_ids)
    if actual_line_ids != requested_line_ids:
        raise TaskSuggestionResponseError(
            "GPT-5.1 did not return exactly one result per requested line.",
            context={
                "missing_line_ids": sorted(
                    requested_line_ids - actual_line_ids
                ),
                "unexpected_line_ids": sorted(
                    actual_line_ids - requested_line_ids
                ),
            },
        )

    validated: Dict[str, ModelTaskSuggestion] = {}
    for result in response.lines:
        allowed_codes = {
            task.task_code for task in candidate_sets[result.line_id]
        }
        allowed_refs = {f"line:{result.line_id}"} | {
            f"task:{code}" for code in allowed_codes
        }
        if result.task_code is not None:
            if result.task_code not in governed_codes:
                raise TaskSuggestionResponseError(
                    "GPT-5.1 returned a task code outside TaskMaster.",
                    context={
                        "line_id": result.line_id,
                        "task_code": result.task_code,
                    },
                )
            if result.task_code not in allowed_codes:
                raise TaskSuggestionResponseError(
                    "GPT-5.1 returned a task code outside the retrieved "
                    "candidate set.",
                    context={
                        "line_id": result.line_id,
                        "task_code": result.task_code,
                    },
                )
        unknown_refs = sorted(set(result.evidence_refs) - allowed_refs)
        if unknown_refs:
            raise TaskSuggestionResponseError(
                "GPT-5.1 returned evidence outside the governed request.",
                context={
                    "line_id": result.line_id,
                    "unknown_evidence_refs": unknown_refs,
                },
            )
        if f"line:{result.line_id}" not in result.evidence_refs:
            raise TaskSuggestionResponseError(
                "GPT-5.1 omitted the required line evidence reference.",
                context={"line_id": result.line_id},
            )
        if result.task_code is not None:
            if f"task:{result.task_code}" not in result.evidence_refs:
                raise TaskSuggestionResponseError(
                    "GPT-5.1 omitted the selected task evidence reference.",
                    context={
                        "line_id": result.line_id,
                        "task_code": result.task_code,
                    },
                )
        validated[result.line_id] = result
    return validated


def suggest_tasks_for_invoice(
    package: NormalizedInvoicePackage,
    snapshot: TaskMasterSnapshot,
    *,
    workflow_run_id: str,
    model: Optional[TaskSuggestionModel] = None,
    model_deployment: str = DEFAULT_MODEL_DEPLOYMENT,
    prompt_version: str = DEFAULT_TASK_PROMPT_VERSION,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    created_at_utc: Optional[datetime] = None,
) -> List[TaskDetermination]:
    """Determine governed task codes with at most one model call per invoice."""
    if candidate_limit < 1:
        raise ValueError("candidate_limit must be at least 1")
    created_at = created_at_utc or datetime.now(timezone.utc)
    snapshot_sha256 = _task_snapshot_sha256(snapshot)
    tasks_by_code = {task.task_code: task for task in snapshot.tasks}
    tasks_by_description: Dict[str, List[TaskReferenceEntry]] = {}
    for task in snapshot.tasks:
        tasks_by_description.setdefault(
            _normalized_text(task.description),
            [],
        ).append(task)

    determinations: Dict[str, TaskDetermination] = {}
    candidate_sets: Dict[str, Sequence[TaskReferenceEntry]] = {}
    lines_by_id = {line.line_id: line for line in package.lines}
    invoice_query = " ".join(
        [package.vendor_name]
        + [line.description for line in package.lines]
        + [
            line.part_number
            for line in package.lines
            if line.part_number
        ]
    )
    invoice_candidates = rank_task_candidates(
        invoice_query,
        snapshot,
        limit=min(candidate_limit, _SHARED_CANDIDATE_LIMIT),
    )

    for line in package.lines:
        exact = _exact_task(
            line,
            tasks_by_code,
            tasks_by_description,
        )
        if exact is not None:
            determinations[line.line_id] = _determination(
                package=package,
                line=line,
                workflow_run_id=workflow_run_id,
                prompt_version=prompt_version,
                snapshot=snapshot,
                snapshot_sha256=snapshot_sha256,
                created_at_utc=created_at,
                status=TaskResolutionStatus.RESOLVED,
                match_method=TaskMatchMethod.EXACT_TASK,
                confidence=1.0,
                rationale=(
                    "The source task code or normalized description exactly "
                    "matches the governed TaskMaster."
                ),
                evidence_refs=[
                    f"line:{line.line_id}",
                    f"task:{exact.task_code}",
                ],
                task=exact,
            )
            continue

        if line.source_task_code:
            determinations[line.line_id] = _determination(
                package=package,
                line=line,
                workflow_run_id=workflow_run_id,
                prompt_version=prompt_version,
                snapshot=snapshot,
                snapshot_sha256=snapshot_sha256,
                created_at_utc=created_at,
                status=TaskResolutionStatus.REVIEW,
                match_method=TaskMatchMethod.REVIEW,
                confidence=0.0,
                rationale=(
                    "The supplied source task code is not present in the "
                    "governed TaskMaster and was not remapped."
                ),
                evidence_refs=[f"line:{line.line_id}"],
                unresolved_reason=(
                    f"Source task code {line.source_task_code} has no "
                    "governed TaskMaster mapping."
                ),
            )
            continue

        if _normalized_text(line.description) in (
            _ANCILLARY_REVIEW_DESCRIPTIONS
        ):
            determinations[line.line_id] = _determination(
                package=package,
                line=line,
                workflow_run_id=workflow_run_id,
                prompt_version=prompt_version,
                snapshot=snapshot,
                snapshot_sha256=snapshot_sha256,
                created_at_utc=created_at,
                status=TaskResolutionStatus.REVIEW,
                match_method=TaskMatchMethod.REVIEW,
                confidence=0.0,
                rationale=(
                    "Ancillary shipping or freight cannot be assigned to "
                    "a product task without an approved allocation rule."
                ),
                evidence_refs=[f"line:{line.line_id}"],
                unresolved_reason=(
                    "No governed standalone shipping or freight task is "
                    "available."
                ),
            )
            continue

        query = " ".join(
            value
            for value in (line.description, line.part_number)
            if value
        )
        candidates = _merge_candidates(
            rank_task_candidates(
                query,
                snapshot,
                limit=candidate_limit,
            ),
            invoice_candidates,
            limit=candidate_limit,
        )
        if not candidates:
            determinations[line.line_id] = _determination(
                package=package,
                line=line,
                workflow_run_id=workflow_run_id,
                prompt_version=prompt_version,
                snapshot=snapshot,
                snapshot_sha256=snapshot_sha256,
                created_at_utc=created_at,
                status=TaskResolutionStatus.REVIEW,
                match_method=TaskMatchMethod.REVIEW,
                confidence=0.0,
                rationale=(
                    "Deterministic retrieval found no governed TaskMaster "
                    "candidate for the invoice line."
                ),
                evidence_refs=[f"line:{line.line_id}"],
                unresolved_reason=(
                    "No governed TaskMaster candidate was retrieved."
                ),
            )
            continue
        candidate_sets[line.line_id] = candidates

    if candidate_sets:
        if model is None:
            raise TaskSuggestionConfigurationError(
                "A LangChain task-suggestion model is required for "
                "candidate-backed unresolved lines."
            )
        request = {
            "schemaVersion": "2.0-task-suggestion-request",
            "invoiceId": package.invoice_id,
            "packageId": package.package_id,
            "vendorName": package.vendor_name,
            "taskMasterVersion": snapshot.source.source_version,
            "taskMasterSha256": snapshot_sha256,
            "promptVersion": prompt_version,
            "lines": [
                _candidate_payload(lines_by_id[line_id], candidates)
                for line_id, candidates in candidate_sets.items()
            ],
        }
        response = model.suggest(request)
        model_results = _validate_model_response(
            response,
            candidate_sets,
            set(tasks_by_code),
        )
        for line_id, result in model_results.items():
            line = lines_by_id[line_id]
            if result.resolution_status == "REVIEW":
                determinations[line_id] = _determination(
                    package=package,
                    line=line,
                    workflow_run_id=workflow_run_id,
                    prompt_version=prompt_version,
                    snapshot=snapshot,
                    snapshot_sha256=snapshot_sha256,
                    created_at_utc=created_at,
                    status=TaskResolutionStatus.REVIEW,
                    match_method=TaskMatchMethod.REVIEW,
                    confidence=result.confidence,
                    rationale=result.rationale,
                    evidence_refs=result.evidence_refs,
                    item_identified=result.item_identified,
                    unresolved_reason=result.unresolved_reason,
                    model_deployment=model_deployment,
                )
                continue
            task = tasks_by_code[result.task_code or ""]
            determinations[line_id] = _determination(
                package=package,
                line=line,
                workflow_run_id=workflow_run_id,
                prompt_version=prompt_version,
                snapshot=snapshot,
                snapshot_sha256=snapshot_sha256,
                created_at_utc=created_at,
                status=TaskResolutionStatus.RESOLVED,
                match_method=TaskMatchMethod.LANGCHAIN,
                confidence=result.confidence,
                rationale=result.rationale,
                evidence_refs=result.evidence_refs,
                task=task,
                item_identified=result.item_identified,
                model_deployment=model_deployment,
            )

    return [determinations[line.line_id] for line in package.lines]


__all__ = [
    "AutarchLangChainChatModel",
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_MODEL_DEPLOYMENT",
    "DEFAULT_TASK_PROMPT_VERSION",
    "LangChainTaskSuggestionModel",
    "ModelTaskSuggestion",
    "ModelTaskSuggestionBatch",
    "TaskSuggestionConfigurationError",
    "TaskSuggestionResponseError",
    "build_azure_task_suggestion_model",
    "suggest_tasks_for_invoice",
]
