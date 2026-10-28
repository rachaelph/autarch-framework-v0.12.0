"""Data models for Invoice Agent Invocation Function."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskSuggestionLine(BaseModel):
    """Task suggestion for a single invoice line."""

    line_id: str
    recommendation: str = Field(..., pattern="^(CAPEX|OPEX|REVIEW)$")
    task_code: Optional[str] = None
    asset_class_code: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str
    evidence_refs: List[str] = Field(default_factory=list)
    policy_ids: List[str] = Field(default_factory=list)


class TaskSuggestion(BaseModel):
    """Complete task suggestion for an invoice."""

    model_config = ConfigDict(
        json_encoders={datetime: lambda v: v.isoformat()}
    )

    task_suggestion_id: str = Field(default_factory=lambda: str(uuid4()))
    package_id: str
    invoice_id: str
    run_id: str
    correlation_id: str
    lines: List[TaskSuggestionLine]
    overall_recommendation: str = Field(..., pattern="^(CAPEX|OPEX|REVIEW)$")
    agent_execution_id: Optional[str] = None
    agent_session_id: Optional[str] = None
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "PENDING"


class InvoiceReadyMessage(BaseModel):
    """Parsed InvoiceReady v1 Service Bus message."""

    event_id: str
    event_type: str
    schema_version: str
    invoice_id: str
    package_id: str
    run_id: str
    package_uri: str
    occurred_at_utc: str
    correlation_id: str

    @classmethod
    def from_properties(cls, props: Mapping[str, Any]) -> InvoiceReadyMessage:
        """Parse from Service Bus application properties."""
        return cls(
            event_id=props.get("eventId", ""),
            event_type=props.get("eventType", ""),
            schema_version=props.get("schemaVersion", "1.0"),
            invoice_id=props.get("invoiceId", ""),
            package_id=props.get("packageId", ""),
            run_id=props.get("runId", ""),
            package_uri=props.get("packageUri", ""),
            occurred_at_utc=props.get("occurredAtUtc", ""),
            correlation_id=props.get("correlationId", ""),
        )


class FoundryAgentRequest(BaseModel):
    """Request to send to Foundry agent."""

    invoice_package: Dict[str, Any]
    correlation_id: str
    package_id: str
    run_id: str


class FoundryAgentResponse(BaseModel):
    """Expected response structure from agent (adjust based on actual agent output)."""

    lines: List[TaskSuggestionLine]
    overall_recommendation: str
    session_id: Optional[str] = None
    execution_id: Optional[str] = None


__all__ = [
    "InvoiceReadyMessage",
    "TaskSuggestionLine",
    "TaskSuggestion",
    "FoundryAgentRequest",
    "FoundryAgentResponse",
]
