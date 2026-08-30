"""Unit tests for the LLMClient abstraction (T017a).

Covers RuleBasedStubClient deterministically with zero external calls, provider selection
(including rejecting an unsupported/local provider value), and FreeTierHostedLLMClient's
Groq integration against a mocked httpx transport — no real network call anywhere in this
file, matching this repo's convention (real-dependency tests are opt-in and skip by default,
see tests/contract/test_adapter_contract_prestashop.py).
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.agent import turn_context
from src.agent.llm_client import (
    FreeTierHostedLLMClient,
    HostedPaidLLMClient,
    RuleBasedStubClient,
    create_llm_client,
)


@pytest.fixture
def stub() -> RuleBasedStubClient:
    return RuleBasedStubClient()


def test_stub_recognizes_search_intent(stub: RuleBasedStubClient) -> None:
    action = stub.parse_turn("show me red t-shirts", {})
    assert action.action_type == "search_products"
    assert action.parameters["query"] == "show me red t-shirts"


def test_stub_recognizes_add_to_cart_intent(stub: RuleBasedStubClient) -> None:
    action = stub.parse_turn("add the blue jacket to my cart", {})
    assert action.action_type == "propose_add_to_cart"


def test_stub_recognizes_confirm_intent(stub: RuleBasedStubClient) -> None:
    action = stub.parse_turn("yes, do it", {})
    assert action.action_type == "confirm_pending_action"


def test_stub_recognizes_decline_intent(stub: RuleBasedStubClient) -> None:
    action = stub.parse_turn("no, cancel that", {})
    assert action.action_type == "decline_pending_action"


def test_stub_recognizes_checkout_intent(stub: RuleBasedStubClient) -> None:
    action = stub.parse_turn("I'd like to checkout now", {})
    assert action.action_type == "request_checkout"


def test_create_llm_client_defaults_to_free_tier_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "fake-key-for-test")
    client = create_llm_client()
    assert isinstance(client, FreeTierHostedLLMClient)


def test_create_llm_client_rule_based_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_llm_client(provider="rule-based-stub")
    assert isinstance(client, RuleBasedStubClient)


def test_create_llm_client_hosted_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = create_llm_client(provider="hosted-paid")
    assert isinstance(client, HostedPaidLLMClient)


def test_create_llm_client_rejects_local_ollama_provider() -> None:
    """local/Ollama was deliberately evaluated and rejected for this project
    (research.md §3a) — must not be a selectable provider."""
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        create_llm_client(provider="local")


def test_create_llm_client_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        create_llm_client(provider="not-a-real-provider")


def test_free_tier_hosted_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        FreeTierHostedLLMClient()


# -- FreeTierHostedLLMClient / Groq, against a mocked httpx transport -------------------- #


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _groq_response(*, name: str, arguments: dict, prompt_tokens: int = 42, completion_tokens: int = 8):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": name, "arguments": json.dumps(arguments)}}
                        ]
                    }
                }
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


def test_groq_client_calls_the_right_tool_and_returns_its_arguments() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tool_choice"] == "required"
        assert {t["function"]["name"] for t in body["tools"]} >= {"search_products", "request_checkout"}
        return _groq_response(name="propose_add_to_cart", arguments={"raw_text": "add the red one"})

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("add the red one", {})
    assert action.action_type == "propose_add_to_cart"
    assert action.parameters == {"raw_text": "add the red one"}


def test_groq_client_records_token_usage_and_latency_on_turn_context() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return _groq_response(name="search_products", arguments={"query": "shirts"}, prompt_tokens=100, completion_tokens=20)

    client = FreeTierHostedLLMClient(api_key="fake-key", model="llama-3.3-70b-versatile", client=_mock_client(handler))
    with turn_context.turn_scope(tenant_id=None, session_id="s1") as turn:
        client.parse_turn("show me shirts", {})
        assert turn.llm_provider == "free-tier-hosted"
        assert turn.llm_model == "llama-3.3-70b-versatile"
        assert turn.prompt_tokens == 100
        assert turn.completion_tokens == 20
        assert turn.llm_ms is not None and turn.llm_ms >= 0


def test_groq_client_falls_back_to_search_on_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("do something", {})
    assert action.action_type == "search_products"
    assert action.parameters == {"query": "do something"}


def test_groq_client_falls_back_to_search_on_malformed_tool_arguments() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"tool_calls": [{"function": {"name": "search_products", "arguments": "{not json"}}]}}
                ],
                "usage": {},
            },
        )

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("hello", {})
    assert action.action_type == "search_products"
    assert action.parameters == {"query": "hello"}


def test_groq_client_falls_back_to_search_on_unrecognized_tool_name() -> None:
    """research.md §9.6 (prompt-injection hygiene): a tool name the schema never offered
    must never reach dialogue.py — that's the LLM (or a manipulated response) trying to
    call something outside the fixed action vocabulary."""

    def handler(_: httpx.Request) -> httpx.Response:
        return _groq_response(name="delete_all_orders", arguments={})

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("hello", {})
    assert action.action_type == "search_products"


def test_groq_client_falls_back_to_search_when_no_tool_call_is_returned() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {}}], "usage": {}})

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("hello", {})
    assert action.action_type == "search_products"


def test_groq_client_retries_once_on_429_then_succeeds() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return _groq_response(name="search_products", arguments={"query": "shoes"})

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("shoes please", {})
    assert action.action_type == "search_products"
    assert calls["count"] == 2


def test_groq_client_logs_an_error_event_on_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    logged = {}

    def fake_log_action(session_id, intent, action, outcome, *, details=None):
        logged.update(session_id=session_id, intent=intent, outcome=outcome, details=details)

    monkeypatch.setattr("src.logging.audit.log_action", fake_log_action)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    client.parse_turn("hello", {}, session_id="session-42")

    assert logged["session_id"] == "session-42"
    assert logged["intent"] == "llm_call"
    assert logged["outcome"] == "error"


def test_groq_client_context_includes_pending_action_for_the_model() -> None:
    seen_content = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_content["user_message"] = body["messages"][1]["content"]
        return _groq_response(name="confirm_pending_action", arguments={})

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn(
        "yes",
        {"pending_action": {"action_type": "add_cart_item", "recap_text": "Add 1x Red T-Shirt ($19.99)?"}},
    )
    assert action.action_type == "confirm_pending_action"
    assert "Red T-Shirt" in seen_content["user_message"]


def test_groq_client_context_includes_last_shown_products_for_the_model() -> None:
    seen_content = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_content["user_message"] = body["messages"][1]["content"]
        return _groq_response(name="ask_or_chat", arguments={"text": "It's a men's t-shirt, so yes!"})

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn(
        "does it fit a young man?",
        {"last_shown_products": "Hummingbird printed t-shirt ($23.90)"},
    )
    assert action.action_type == "ask_or_chat"
    assert "Hummingbird printed t-shirt" in seen_content["user_message"]


def test_groq_client_returns_ask_or_chat_for_a_greeting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert {t["function"]["name"] for t in body["tools"]} >= {"ask_or_chat"}
        return _groq_response(
            name="ask_or_chat",
            arguments={"text": "Hi there! What are you shopping for today?"},
        )

    client = FreeTierHostedLLMClient(api_key="fake-key", client=_mock_client(handler))
    action = client.parse_turn("hello", {})
    assert action.action_type == "ask_or_chat"
    assert action.parameters == {"text": "Hi there! What are you shopping for today?"}
