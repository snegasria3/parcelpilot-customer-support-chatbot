from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.auth import ScopedRecordNotFound
from backend.llm import (
    GroqStructuredLLM,
    LLMUnavailable,
    deterministic_answer,
    heuristic_plan,
    stated_delay_hours,
    validate_grounded_answer,
)
from backend.schemas import (
    ActionType,
    ChatResponse,
    Confidence,
    CustomerIdentity,
    Decision,
    Intent,
    PendingAction,
    ToolEvent,
)
from backend.tools.registry import AgentTools

logger = logging.getLogger(__name__)


def _event(tool: str, status: str, summary: str, started: float) -> ToolEvent:
    return ToolEvent(
        tool=tool,
        status=status,
        summary=summary,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


class CustomerSupportAgent:
    def __init__(
        self,
        *,
        tools: AgentTools,
        llm: GroqStructuredLLM | None,
        allow_safe_fallback: bool,
    ):
        self.tools = tools
        self.llm = llm
        self.allow_safe_fallback = allow_safe_fallback

    @staticmethod
    def _blocked(summary: str) -> Decision:
        return Decision(
            outcome="blocked",
            summary=summary,
            uncertainty=["Customer access is restricted at the data and tool layer."],
            confidence=Confidence.HIGH,
        )

    @staticmethod
    def _generic(message: str) -> Decision:
        if re.fullmatch(r"\s*(?:hi|hello|hey|good (?:morning|afternoon|evening))[!. ]*", message, re.I):
            return Decision(
                outcome="answer",
                summary="Hello! I can help with your ParcelPilot orders, cancellations, pickup credits, tickets, support targets, plan, and documented product issues.",
                confidence=Confidence.HIGH,
            )
        if re.search(
            r"system prompt|hidden prompt|developer message|api key|password|show secrets|ignore (?:all|previous).*instructions",
            message,
            re.I,
        ):
            return Decision(
                outcome="blocked",
                summary="I cannot reveal hidden instructions, credentials, secrets, or internal system data. I can help with your authenticated ParcelPilot account.",
                confidence=Confidence.HIGH,
            )
        return Decision(
            outcome="clarify",
            summary="I can answer this only from your authenticated account and the supplied ParcelPilot sources.",
            uncertainty=["Please include an order ID, ticket ID, or a specific policy, plan, or product question."],
            confidence=Confidence.LOW,
        )

    def run(
        self,
        *,
        message: str,
        identity: CustomerIdentity,
        conversation_id: str | None = None,
    ) -> ChatResponse:
        trace_id = str(uuid.uuid4())
        conversation_id = conversation_id or f"CONV-{uuid.uuid4().hex[:16]}"
        events: list[ToolEvent] = []
        account = self.tools.customer_data.account(identity)

        started = time.perf_counter()
        if self.tools.customer_data.has_foreign_account_reference(identity, message):
            decision = self._blocked(
                "I can only access information belonging to your authenticated customer account. I cannot search or discuss another customer's records or agreement."
            )
            events.append(
                _event(
                    "authorize_customer_scope",
                    "blocked",
                    "Cross-account request blocked before model or data access.",
                    started,
                )
            )
            return self._finalize(
                trace_id=trace_id,
                conversation_id=conversation_id,
                message=message,
                identity=identity,
                decision=decision,
                citations=[],
                events=events,
                pending_action=None,
                retrieval_mode="not_needed",
            )
        events.append(
            _event("authorize_customer_scope", "completed", f"Request locked to {identity.account_id}.", started)
        )

        started = time.perf_counter()
        mode = "safe_fallback"
        try:
            if self.llm is None:
                raise LLMUnavailable("Groq is not configured")
            plan = self.llm.plan(message)
            mode = "llm"
            plan_summary = (
                "Groq returned a Pydantic-validated tool plan; exact IDs were reconciled from the original message."
            )
        except LLMUnavailable:
            if not self.allow_safe_fallback:
                raise
            plan = heuristic_plan(message)
            plan_summary = "The transparent local planner selected tools because Groq was unavailable."
        events.append(_event("understand_request", "completed", plan_summary, started))

        started = time.perf_counter()
        orders: list[dict[str, Any]] = []
        tickets: list[dict[str, Any]] = []
        try:
            orders = [self.tools.customer_data.order(identity, order_id) for order_id in plan.order_ids]
            tickets = [self.tools.customer_data.ticket(identity, ticket_id) for ticket_id in plan.ticket_ids]
            events.append(
                _event(
                    self.tools.customer_data.name,
                    "completed" if orders or tickets or plan.needs_structured_data else "skipped",
                    f"Loaded {len(orders)} order(s), {len(tickets)} ticket(s), and the signed-in account through scoped queries.",
                    started,
                )
            )
        except ScopedRecordNotFound:
            decision = self._blocked(
                "I couldn't find that record in your authenticated account. Check the ID or contact support."
            )
            events.append(
                _event(
                    self.tools.customer_data.name,
                    "blocked",
                    "Record missing or outside the authenticated account; no existence detail was exposed.",
                    started,
                )
            )
            return self._finalize(
                trace_id=trace_id,
                conversation_id=conversation_id,
                message=message,
                identity=identity,
                decision=decision,
                citations=[],
                events=events,
                pending_action=None,
                retrieval_mode="not_needed",
                preferred_mode=mode,
            )

        started = time.perf_counter()
        decisions: list[Decision] = []
        intents = set(plan.intents)
        for order in orders:
            if Intent.ORDER_STATUS in intents:
                decisions.append(self.tools.customer_data.policies.order_status(order, account))
            if Intent.CANCELLATION in intents:
                decisions.append(self.tools.customer_data.policies.cancellation(order, account))
            if Intent.SERVICE_CREDIT in intents:
                decisions.append(self.tools.customer_data.policies.service_credit(order, account))
        for ticket in tickets:
            if Intent.TICKET_STATUS in intents:
                decisions.append(self.tools.customer_data.policies.ticket_status(ticket))
            if Intent.SLA in intents or plan.requested_action == ActionType.CREATE_ESCALATION:
                decisions.append(self.tools.customer_data.policies.sla(ticket, account))
            if Intent.KNOWN_ISSUE in intents:
                known = self.tools.customer_data.policies.known_issue(ticket)
                if known:
                    decisions.append(known)
            if Intent.SOURCE_RELIABILITY in intents:
                conflict = self.tools.customer_data.policies.historical_conflict(ticket, account)
                if conflict:
                    decisions.append(conflict)
        if Intent.CANCELLATION in intents and not orders:
            decisions.append(self.tools.customer_data.policies.cancellation_guidance(account))
        if Intent.SERVICE_CREDIT in intents and not orders:
            decisions.append(
                self.tools.customer_data.policies.service_credit_guidance(account, stated_delay_hours(message))
            )
        if Intent.ACCOUNT_ENTITLEMENT in intents:
            decisions.append(self.tools.customer_data.policies.account_entitlement(account, message))
        if Intent.SOURCE_RELIABILITY in intents and not any(ticket.get("historical_resolution") for ticket in tickets):
            decisions.append(self.tools.customer_data.policies.source_reliability(account))
        if Intent.KNOWN_ISSUE in intents and not tickets:
            known = self.tools.customer_data.policies.known_issue({"subject": message, "description": ""})
            if known:
                decisions.append(known)
            else:
                decisions.append(
                    Decision(
                        outcome="clarify",
                        summary="I found no verified current known-issue match from the information provided.",
                        uncertainty=[
                            "Provide the ticket ID, exact error, affected workflow, and impact so support can investigate."
                        ],
                        confidence=Confidence.LOW,
                        source_files=["04_Product_Operations_Guide_and_Known_Issues.pdf"],
                    )
                )
        if not decisions:
            decisions.append(self._generic(message))
        decision = self.tools.customer_data.policies.merge(decisions)
        events.append(
            _event(
                "deterministic_policy_engine",
                "blocked" if decision.outcome == "blocked" else "completed",
                "Applied source precedence, exact business rules, calculations, and snapshot-time logic.",
                started,
            )
        )

        started = time.perf_counter()
        citations = []
        retrieval_mode = "not_needed"
        if plan.needs_documents or decision.source_files:
            try:
                retrieved, retrieval_mode = self.tools.document_search.search(
                    identity,
                    message,
                    decision.source_files,
                )
                citations = self.tools.document_search.retriever.citations(retrieved)
                events.append(
                    _event(
                        self.tools.document_search.name,
                        "completed",
                        f"Retrieved {len(citations)} current, account-authorized passage(s) using {retrieval_mode}.",
                        started,
                    )
                )
            except Exception as exc:
                decision = decision.model_copy(
                    update={
                        "outcome": "clarify",
                        "confidence": Confidence.LOW,
                        "uncertainty": [
                            *decision.uncertainty,
                            "The semantic evidence index is unavailable; support should verify the source document.",
                        ],
                    }
                )
                retrieval_mode = "unavailable"
                events.append(
                    _event(
                        self.tools.document_search.name,
                        "failed",
                        "Vector search failed safely; no document text was invented.",
                        started,
                    )
                )
                logger.warning("retrieval_failed", extra={"trace_id": trace_id, "error_type": type(exc).__name__})
        else:
            events.append(
                _event(self.tools.document_search.name, "skipped", "Document evidence was not required.", started)
            )

        started = time.perf_counter()
        pending_action: PendingAction | None = None
        if plan.requested_action and decision.outcome != "blocked":
            target_id = tickets[0]["ticket_id"] if tickets else orders[0]["order_id"] if orders else None
            action_label = "escalation" if plan.requested_action == ActionType.CREATE_ESCALATION else "follow-up task"
            summary = f"Prepare {action_label} for {target_id or account['account_name']}: {decision.summary[:300]}"
            pending_action = self.tools.customer_action.prepare(
                identity=identity,
                action_type=plan.requested_action,
                target_id=target_id,
                summary=summary,
                payload={
                    "trace_id": trace_id,
                    "target_id": target_id,
                    "account_id": identity.account_id,
                    "reason": decision.summary,
                    "confidence": decision.confidence.value,
                },
            )
            decision = decision.model_copy(
                update={
                    "outcome": "action_prepared",
                    "summary": decision.summary + f" A {action_label} has been prepared but not executed.",
                }
            )
            events.append(
                _event(
                    self.tools.customer_action.name,
                    "completed",
                    f"Prepared a scoped {action_label}; confirmation is still required.",
                    started,
                )
            )
        else:
            events.append(
                _event(self.tools.customer_action.name, "skipped", "No state-changing action was requested.", started)
            )

        return self._finalize(
            trace_id=trace_id,
            conversation_id=conversation_id,
            message=message,
            identity=identity,
            decision=decision,
            citations=citations,
            events=events,
            pending_action=pending_action,
            retrieval_mode=retrieval_mode,
            preferred_mode=mode,
        )

    def _finalize(
        self,
        *,
        trace_id: str,
        conversation_id: str,
        message: str,
        identity: CustomerIdentity,
        decision: Decision,
        citations: list[Any],
        events: list[ToolEvent],
        pending_action: PendingAction | None,
        retrieval_mode: str,
        preferred_mode: str = "safe_fallback",
    ) -> ChatResponse:
        started = time.perf_counter()
        answer = deterministic_answer(decision, citations, pending_action)
        mode = "safe_fallback"
        if self.llm is not None and preferred_mode == "llm":
            try:
                generated = self.llm.compose(
                    message=message,
                    decision=decision,
                    citations=citations,
                    pending_action=pending_action,
                )
                validate_grounded_answer(
                    generated,
                    message=message,
                    decision=decision,
                    citations=citations,
                    pending_action=pending_action,
                )
                answer, mode = generated, "llm"
            except LLMUnavailable as exc:
                logger.warning(
                    "answer_validation_fallback", extra={"trace_id": trace_id, "error_type": type(exc).__name__}
                )
        events.append(
            _event(
                "compose_grounded_answer",
                "completed",
                "Used validated LLM wording."
                if mode == "llm"
                else "Used the deterministic evidence-only answer template.",
                started,
            )
        )
        self.tools.customer_data.db.audit(
            trace_id=trace_id,
            account_id=identity.account_id,
            user_id=identity.user_id,
            event_type="chat_completed",
            outcome=decision.outcome,
            metadata={"mode": mode, "retrieval_mode": retrieval_mode, "tools": [event.tool for event in events]},
            created_at=datetime.now(UTC).isoformat(),
        )
        return ChatResponse(
            trace_id=trace_id,
            conversation_id=conversation_id,
            answer=answer,
            confidence=decision.confidence,
            needs_human=bool(
                decision.uncertainty or decision.outcome in {"clarify", "blocked", "escalation_recommended"}
            ),
            mode=mode,
            retrieval_mode=retrieval_mode,
            citations=citations,
            tool_events=events,
            pending_action=pending_action,
        )
