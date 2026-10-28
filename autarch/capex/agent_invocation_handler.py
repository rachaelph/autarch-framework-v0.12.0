"""Invoke the Foundry Invoice Task Determination model."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Mapping, Sequence

from azure.identity import ManagedIdentityCredential

try:
    from agent_framework import Message
    from agent_framework.openai import OpenAIChatClient

    AGENT_FRAMEWORK_AVAILABLE = True
except ImportError:
    AGENT_FRAMEWORK_AVAILABLE = False

from .agent_invocation_models import (
    FoundryAgentResponse,
    TaskSuggestion,
)

logger = logging.getLogger(__name__)


class FoundryAgentError(RuntimeError):
    """Raised when Task Determination invocation fails."""


def _extract_json(response_text: str) -> Mapping[str, Any]:
    text = response_text.strip()

    if text.startswith("```json"):
        text = text[7:]
        if "```" in text:
            text = text[: text.index("```")]
    elif text.startswith("```"):
        text = text[3:]
        if "```" in text:
            text = text[: text.index("```")]

    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise FoundryAgentError(
            "Foundry returned an invalid JSON response."
        ) from exc

    if not isinstance(value, Mapping):
        raise FoundryAgentError(
            "Foundry response must be a JSON object."
        )

    return value


def _catalog_map(
    task_catalog: Sequence[Mapping[str, Any]],
) -> Dict[str, str]:
    catalog: Dict[str, str] = {}

    for item in task_catalog:
        task_code = str(item.get("task_code", "")).strip()
        description = str(item.get("description", "")).strip()

        if not task_code or not description:
            raise FoundryAgentError(
                "TaskMaster contains an incomplete task."
            )
        if task_code in catalog:
            raise FoundryAgentError(
                f"TaskMaster contains duplicate task code {task_code}."
            )

        catalog[task_code] = description

    if not catalog:
        raise FoundryAgentError(
            "TaskMaster contains no active tasks."
        )

    return catalog


def _line_id(line: Mapping[str, Any], index: int) -> str:
    return str(
        line.get("invoiceLineId")
        or line.get("lineId")
        or line.get("position")
        or index
    )


def _build_prompt(
    *,
    invoice_package: Mapping[str, Any],
    invoice_id: str,
    task_catalog: Mapping[str, str],
) -> str:
    invoice = invoice_package.get("invoice", {})
    lines = invoice_package.get("lines", [])

    task_text = "\n".join(
        f"{task_code}|{description}"
        for task_code, description in sorted(
            task_catalog.items()
        )
    )

    line_text = "\n".join(
        (
            f"{_line_id(line, index)}|"
            f"{line.get('description', '')}|"
            f"{line.get('partNumber') or line.get('articleNumber') or ''}|"
            f"{line.get('quantity', '')}|"
            f"{line.get('unitCost') or line.get('unitPrice') or ''}"
        )
        for index, line in enumerate(lines, start=1)
    )

    vendor_name = (
        invoice.get("vendorName")
        or invoice.get("vendor", {}).get("name")
        or ""
    )

    return f"""
Determine one governed task result for every invoice line.

Invoice ID: {invoice_id}
Invoice number: {invoice.get("invoiceNumber") or invoice.get("invoiceId") or ""}
Vendor: {vendor_name}
Country/region: {invoice.get("countryRegion") or "US"}

Allowed tasks are listed as taskCode|description:
{task_text}

Invoice lines are listed as lineId|description|partNumber|quantity|unitCost:
{line_text}

Rules:
- Return exactly one result for every input lineId.
- Use only a task_code from the allowed task list.
- If no task is sufficiently supported, set task_code to null.
- Never invent a task code.
- This step does not make a CapEx or OpEx decision. Set recommendation to REVIEW.
- Confidence must be between 0 and 1.
- Rationale must explain the task match or why the line is unresolved.
- Return JSON only.

Response schema:
{{
  "lines": [
    {{
      "line_id": "<exact input lineId>",
      "recommendation": "REVIEW",
      "task_code": "<allowed code or null>",
      "asset_class_code": null,
      "confidence": 0.0,
      "rationale": "...",
      "evidence_refs": [],
      "policy_ids": []
    }}
  ],
  "overall_recommendation": "REVIEW"
}}
""".strip()


def invoke_task_determination_agent(
    *,
    invoice_package: Dict[str, Any],
    correlation_id: str,
    package_id: str,
    run_id: str,
    invoice_id: str,
    foundry_endpoint: str,
    managed_identity_client_id: str,
    task_catalog: Sequence[Mapping[str, Any]],
    deployment_name: str = "gpt-6.1-sol",
) -> TaskSuggestion:
    """Invoke Foundry and return validated line-level task results."""

    if not AGENT_FRAMEWORK_AVAILABLE:
        raise FoundryAgentError(
            "agent-framework SDK not available."
        )
    if not foundry_endpoint.strip():
        raise FoundryAgentError(
            "Foundry endpoint is required."
        )
    if not managed_identity_client_id.strip():
        raise FoundryAgentError(
            "Managed identity client ID is required."
        )

    lines = invoice_package.get("lines", [])
    if not lines:
        raise FoundryAgentError(
            "Invoice package contains no lines."
        )

    catalog = _catalog_map(task_catalog)
    expected_line_ids = {
        _line_id(line, index)
        for index, line in enumerate(lines, start=1)
    }

    credential = ManagedIdentityCredential(
        client_id=managed_identity_client_id,
    )

    try:
        access_token = credential.get_token(
            "https://ai.azure.com/.default"
        )
        client = OpenAIChatClient(
            model=deployment_name,
            api_key=access_token.token,
            base_url=(
                f"{foundry_endpoint.rstrip('/')}/openai/v1/"
            ),
        )

        messages = [
            Message(
                "system",
                [
                    (
                        "You are the Circle K Invoice Task "
                        "Determination Agent. Select only governed "
                        "TaskMaster codes and return strict JSON."
                    )
                ],
            ),
            Message(
                "user",
                [
                    _build_prompt(
                        invoice_package=invoice_package,
                        invoice_id=invoice_id,
                        task_catalog=catalog,
                    )
                ],
            ),
        ]

        logger.info(
            "foundry_agent_invocation_started",
            extra={
                "invoiceId": invoice_id,
                "packageId": package_id,
                "correlationId": correlation_id,
                "lineCount": len(lines),
                "taskCount": len(catalog),
                "deploymentName": deployment_name,
            },
        )

        response = asyncio.run(
            client.get_response(messages)
        )
        response_text = response.text

        if not response_text:
            raise FoundryAgentError(
                "Foundry returned an empty response."
            )

        parsed = FoundryAgentResponse.model_validate(
            _extract_json(response_text)
        )

        actual_line_ids = {
            line.line_id
            for line in parsed.lines
        }
        if len(parsed.lines) != len(expected_line_ids):
            raise FoundryAgentError(
                "Foundry did not return exactly one result "
                "for every invoice line."
            )
        if actual_line_ids != expected_line_ids:
            raise FoundryAgentError(
                "Foundry response line IDs do not match "
                "the invoice package."
            )

        invalid_task_codes = sorted(
            {
                line.task_code
                for line in parsed.lines
                if line.task_code is not None
                and line.task_code not in catalog
            }
        )
        if invalid_task_codes:
            raise FoundryAgentError(
                "Foundry returned task codes outside TaskMaster: "
                f"{invalid_task_codes}"
            )

        task_suggestion = TaskSuggestion(
            package_id=package_id,
            invoice_id=invoice_id,
            run_id=run_id,
            correlation_id=correlation_id,
            lines=parsed.lines,
            overall_recommendation="REVIEW",
            agent_execution_id=response.response_id,
            agent_session_id=response.conversation_id,
            status="PENDING",
        )

        logger.info(
            "foundry_agent_invocation_completed",
            extra={
                "packageId": package_id,
                "invoiceId": invoice_id,
                "lineCount": len(parsed.lines),
                "suggestedCount": sum(
                    line.task_code is not None
                    for line in parsed.lines
                ),
                "unresolvedCount": sum(
                    line.task_code is None
                    for line in parsed.lines
                ),
                "deploymentName": deployment_name,
            },
        )

        return task_suggestion
    except FoundryAgentError:
        raise
    except Exception as exc:
        logger.error(
            "foundry_agent_error",
            extra={
                "packageId": package_id,
                "invoiceId": invoice_id,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise FoundryAgentError(
            "Foundry Task Determination invocation failed: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    finally:
        credential.close()


__all__ = [
    "FoundryAgentError",
    "invoke_task_determination_agent",
]
