"""Unit tests for the LLMClient abstraction (T017a).

Covers RuleBasedStubClient deterministically with zero external calls, and provider
selection (including rejecting an unsupported/local provider value).
"""

from __future__ import annotations

import pytest

from src.agent.llm_client import (
    ActionCall,
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
