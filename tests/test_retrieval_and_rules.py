from __future__ import annotations

from backend.llm import exact_entities, heuristic_plan, validate_grounded_answer
from backend.schemas import Citation, Confidence, Decision, Intent


def test_exact_ids_are_taken_only_from_customer_message():
    orders, tickets = exact_entities("Check ord-1001 and ORD-1002 plus tkt-501")
    assert orders == ["ORD-1001", "ORD-1002"]
    assert tickets == ["TKT-501"]


def test_paraphrased_intents_select_expected_tools():
    plan = heuristic_plan("What will it cost to void ORD-2001, and has pickup been missed?")
    assert Intent.CANCELLATION in plan.intents
    assert Intent.SERVICE_CREDIT in plan.intents
    assert plan.needs_documents is True
    assert plan.needs_structured_data is True
    assert plan.needs_calculation is True


def test_policy_precedence_northstar_override(container, identities):
    decision = container.tools.customer_data.calculate_cancellation(identities["northstar"], "ORD-1001")
    assert decision.fields["fee_inr"] == 0
    assert decision.fields["agreement_override"] is True
    assert decision.source_files[0] == "05_Northstar_Logistics_Enterprise_Agreement.pdf"


def test_lumen_agreement_replaces_default_credit(container, identities):
    decision = container.tools.customer_data.calculate_service_credit(identities["lumenworks"], "ORD-2002")
    assert decision.fields["amount_inr"] == 300
    assert decision.source_files[0] == "06_LumenWorks_Service_Agreement.pdf"
    assert "fixed INR 300" in " ".join(decision.calculations)


def test_historical_resolutions_are_not_authoritative(container, identities):
    account = container.tools.customer_data.account(identities["northstar"])
    ticket = container.tools.customer_data.ticket(identities["northstar"], "TKT-450")
    decision = container.tools.customer_data.policies.historical_conflict(ticket, account)
    assert decision is not None
    assert "conflicts" in decision.summary
    assert "05_Northstar_Logistics_Enterprise_Agreement.pdf" in decision.source_files


def test_required_source_retrieval_is_tenant_filtered(container):
    results, mode = container.retriever.search(
        "fee-free cancellation agreement",
        account_id="ACCT-001",
        required_source_files=["05_Northstar_Logistics_Enterprise_Agreement.pdf"],
    )
    assert mode == "required-source"
    assert any(item.chunk.file_name == "05_Northstar_Logistics_Enterprise_Agreement.pdf" for item in results)
    assert all(item.chunk.account_id in {None, "ACCT-001"} for item in results)


def test_grounding_validator_rejects_unsupported_identifier():
    decision = Decision(outcome="answer", summary="Verified", confidence=Confidence.HIGH)
    try:
        validate_grounded_answer(
            "The answer is about ORD-9999.",
            message="What is the policy?",
            decision=decision,
            citations=[],
            pending_action=None,
        )
    except Exception as exc:
        assert "unsupported identifier" in str(exc)
    else:
        raise AssertionError("Grounding validator accepted an invented ID")


def test_grounding_validator_rejects_unsupported_citation():
    citation = Citation(
        citation_id="D1",
        file_name="source.pdf",
        title="Source",
        section="Section",
        page=1,
        authority="current policy",
        excerpt="Verified source",
    )
    decision = Decision(outcome="answer", summary="Verified", confidence=Confidence.HIGH)
    try:
        validate_grounded_answer(
            "Verified [D2]",
            message="What is the policy?",
            decision=decision,
            citations=[citation],
            pending_action=None,
        )
    except Exception as exc:
        assert "unsupported citation" in str(exc)
    else:
        raise AssertionError("Grounding validator accepted an invented citation")
