from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(StrEnum):
    ORDER_STATUS = "order_status"
    CANCELLATION = "cancellation"
    SERVICE_CREDIT = "service_credit"
    TICKET_STATUS = "ticket_status"
    SLA = "sla"
    KNOWN_ISSUE = "known_issue"
    ACCOUNT_ENTITLEMENT = "account_entitlement"
    SOURCE_RELIABILITY = "source_reliability"
    ESCALATION = "escalation"
    FOLLOW_UP = "follow_up"
    GENERAL = "general"


class ActionType(StrEnum):
    CREATE_ESCALATION = "create_escalation"
    CREATE_FOLLOW_UP = "create_follow_up"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CustomerIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    username: str
    display_name: str
    account_id: str
    token_id: str = ""


class AccountContext(BaseModel):
    account_id: str
    account_name: str
    plan: str
    status: str
    csm: str
    contract_file: str | None = None
    premium_support: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    authenticated: Literal[True] = True
    user: CustomerIdentity
    account: AccountContext
    csrf_token: str
    expires_in_seconds: int


class SessionResponse(BaseModel):
    authenticated: bool
    user: CustomerIdentity | None = None
    account: AccountContext | None = None
    csrf_token: str | None = None
    capabilities: dict[str, str] = Field(default_factory=dict)
    demo_accounts: list[dict[str, str]] = Field(default_factory=list)


class AgentPlan(BaseModel):
    intents: list[Intent] = Field(min_length=1)
    order_ids: list[str] = Field(default_factory=list)
    ticket_ids: list[str] = Field(default_factory=list)
    needs_documents: bool = False
    needs_structured_data: bool = False
    needs_calculation: bool = False
    requested_action: ActionType | None = None
    ambiguity: str | None = None

    @field_validator("order_ids", "ticket_ids")
    @classmethod
    def uppercase_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.upper() for value in values))


class Decision(BaseModel):
    outcome: Literal["answer", "clarify", "blocked", "action_prepared", "escalation_recommended"]
    summary: str
    facts: list[str] = Field(default_factory=list)
    calculations: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.HIGH
    source_files: list[str] = Field(default_factory=list)
    fields: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    citation_id: str
    file_name: str
    title: str
    section: str
    page: int
    authority: str
    excerpt: str
    semantic_score: float = 0.0


class ToolEvent(BaseModel):
    tool: str
    status: Literal["completed", "skipped", "blocked", "failed"]
    summary: str
    duration_ms: int


class PendingAction(BaseModel):
    action_id: str
    action_type: ActionType
    status: Literal["pending", "confirmed", "cancelled", "expired"]
    summary: str
    target_id: str | None = None
    expires_at: str
    requires_confirmation: Literal[True] = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Message cannot be blank")
        return cleaned


class ChatResponse(BaseModel):
    trace_id: str
    conversation_id: str
    answer: str
    confidence: Confidence
    needs_human: bool
    mode: Literal["llm", "safe_fallback"]
    retrieval_mode: Literal["bge+chroma", "required-source", "not_needed", "unavailable"]
    citations: list[Citation]
    tool_events: list[ToolEvent]
    pending_action: PendingAction | None = None


class ActionResult(BaseModel):
    action_id: str
    status: Literal["confirmed", "cancelled"]
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database_ready: bool
    vector_index_ready: bool
    embedding_model: str
    llm: str
    dataset_snapshot: str | None
    warnings: list[str]
