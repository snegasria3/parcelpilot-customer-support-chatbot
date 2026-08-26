from __future__ import annotations

import json

import httpx
import pytest

from backend.llm import GroqStructuredLLM, LLMUnavailable
from backend.schemas import Intent


class StubResponse:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failed", request=request, response=response)

    def json(self):
        return self.payload


def test_groq_plan_uses_authorization_header_json_schema_and_exact_ids(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, request=json, timeout=timeout)
        plan = {
            "intents": ["cancellation"],
            "order_ids": ["ORD-INVENTED"],
            "ticket_ids": [],
            "needs_documents": True,
            "needs_structured_data": True,
            "needs_calculation": True,
            "requested_action": None,
            "ambiguity": None,
        }
        return StubResponse({"choices": [{"message": {"content": __import__("json").dumps(plan)}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    llm = GroqStructuredLLM("test-groq-key", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b")
    plan = llm.plan("Can I cancel ord-1001?")
    assert plan.order_ids == ["ORD-1001"]  # model-proposed IDs are discarded
    assert Intent.CANCELLATION in plan.intents
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer test-groq-key"
    assert captured["request"]["response_format"]["type"] == "json_schema"
    assert captured["request"]["temperature"] == 0


def test_groq_invalid_json_fails_closed(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: StubResponse({"choices": [{"message": {"content": "not-json"}}]}),
    )
    llm = GroqStructuredLLM("test-groq-key", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b")
    with pytest.raises(LLMUnavailable):
        llm.plan("Where is ORD-1001?")


def test_groq_http_error_becomes_safe_provider_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: StubResponse({}, status_code=429))
    llm = GroqStructuredLLM("test-groq-key", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b")
    with pytest.raises(LLMUnavailable):
        llm.plan("Where is ORD-1001?")


def test_groq_key_is_not_serialized_in_request_body(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["body"] = json
        plan = {
            "intents": ["order_status"],
            "order_ids": [],
            "ticket_ids": [],
            "needs_documents": False,
            "needs_structured_data": True,
            "needs_calculation": False,
            "requested_action": None,
            "ambiguity": None,
        }
        return StubResponse({"choices": [{"message": {"content": __import__("json").dumps(plan)}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    llm = GroqStructuredLLM("test-secret-never-serialized", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b")
    llm.plan("Where is ORD-1001?")
    assert "test-secret-never-serialized" not in json.dumps(captured["body"])
