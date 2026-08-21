"""Swappable LLMClient abstraction (research.md §3a, T014a).

Selected at runtime via LLM_PROVIDER env var. Supported profiles (local/Ollama was
deliberately evaluated and rejected for this project, see research.md §3a):

  - free-tier-hosted (default): a free-tier OpenAI-compatible tool-calling API (Groq/Gemini).
  - rule-based-stub: deterministic keyword matcher, zero cost/dependency, used for tests.
  - hosted-paid: stubbed only, out of scope for this internship deliverable.

IMPORTANT (research.md §9.3): the `tools` schema passed to any LLMClient implementation
must only ever contain read-only + propose_action tools — never a direct mutation adapter
method. That restriction lives in agent/intents.py (the caller), not here, but every
LLMClient implementation's docstring reiterates it as a reminder of the capability boundary.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ActionCall:
    """A single structured action the LLM (or the deterministic stub) decided to take,
    matching the fixed action vocabulary (research.md §3)."""

    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    """Common interface: conversation turn text + fixed action schema -> ActionCall.

    Implementations MUST NOT be given (and must not assume access to) any tool that maps to
    a mutating CommerceAdapter method — the only mutation-adjacent tool is `propose_action`,
    which merely creates a PendingAction (research.md §9.3).
    """

    def parse_turn(self, message: str, context: dict) -> ActionCall:
        ...


class RuleBasedStubClient:
    """Deterministic keyword/pattern matcher (T014a, T017a).

    Free, zero external dependency, used as the default for automated tests so the full
    suite runs instantly regardless of any hosted LLM provider's availability/quota. Not
    intended to carry a live, open-ended shopper conversation (can't handle arbitrary
    phrasing) — see research.md §3a.
    """

    _CONFIRM_PATTERNS = re.compile(r"\b(yes|confirm|do it|go ahead|place the order)\b", re.I)
    _DECLINE_PATTERNS = re.compile(r"\b(no|cancel|never mind|don't|stop)\b", re.I)
    _CHECKOUT_PATTERNS = re.compile(r"\b(checkout|check out|place my order)\b", re.I)
    _NAVIGATE_PATTERNS = re.compile(r"\b(take me to|go to|navigate to|show me the)\b", re.I)
    _REMOVE_PATTERNS = re.compile(r"\bremove\b|\bdelete\b", re.I)
    _UPDATE_PATTERNS = re.compile(
        r"\bupdate\b|\bchange (?:the )?quantity\b|\bset (?:the )?quantity\b|\bmake it\b", re.I
    )
    _ADD_PATTERNS = re.compile(r"\badd\b.*\bto\b.*\bcart\b|\badd\b", re.I)

    def parse_turn(self, message: str, context: dict) -> ActionCall:
        text = message.strip()

        if self._CHECKOUT_PATTERNS.search(text):
            return ActionCall(action_type="request_checkout")
        if self._CONFIRM_PATTERNS.search(text):
            return ActionCall(action_type="confirm_pending_action")
        if self._DECLINE_PATTERNS.search(text):
            return ActionCall(action_type="decline_pending_action")
        if self._NAVIGATE_PATTERNS.search(text):
            return ActionCall(action_type="navigate_to", parameters={"target": text})
        if self._REMOVE_PATTERNS.search(text):
            return ActionCall(action_type="propose_remove_from_cart", parameters={"raw_text": text})
        if self._UPDATE_PATTERNS.search(text):
            return ActionCall(action_type="propose_update_cart", parameters={"raw_text": text})
        if self._ADD_PATTERNS.search(text):
            return ActionCall(action_type="propose_add_to_cart", parameters={"raw_text": text})

        # Default: treat anything else as a discovery/search request.
        return ActionCall(action_type="search_products", parameters={"query": text})


class FreeTierHostedLLMClient:
    """Free-tier hosted tool-calling API client (Groq/Gemini free tier, research.md §3a).

    This is a thin, provider-agnostic wrapper: any OpenAI-compatible tool-calling free-tier
    endpoint can be plugged in via LLM_API_KEY/LLM_MODEL (backend/.env.example). Left as a
    minimal, testable skeleton here — full prompt/tool-schema wiring is implemented as part
    of agent/intents.py (T022+), which is responsible for restricting the tool schema to
    read-only + propose_action tools only (research.md §9.3).
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
        if not self.api_key:
            raise ValueError(
                "LLM_API_KEY is required for the free-tier-hosted provider "
                "(backend/.env.example) — get a free key from Groq or Google AI Studio."
            )

    def parse_turn(self, message: str, context: dict) -> ActionCall:
        raise NotImplementedError(
            "FreeTierHostedLLMClient.parse_turn is wired up in agent/intents.py (T022+), "
            "which owns the fixed tool schema and system prompt construction."
        )


class HostedPaidLLMClient:
    """Stub only — out of scope for this internship deliverable (research.md §3a)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key
        self.model = model

    def parse_turn(self, message: str, context: dict) -> ActionCall:
        raise NotImplementedError(
            "hosted-paid is a documented-but-unimplemented profile for a possible future "
            "production upgrade; not needed for this internship deliverable."
        )


_PROVIDERS = {
    "rule-based-stub": RuleBasedStubClient,
    "free-tier-hosted": FreeTierHostedLLMClient,
    "hosted-paid": HostedPaidLLMClient,
}


def create_llm_client(provider: Optional[str] = None, **kwargs: Any) -> LLMClient:
    """Factory: instantiates the right LLMClient implementation for LLM_PROVIDER.

    Raises ValueError for any provider name not in the supported set above — in particular,
    a `local`/Ollama value is deliberately NOT supported (research.md §3a).
    """
    provider = provider or os.environ.get("LLM_PROVIDER", "free-tier-hosted")
    cls = _PROVIDERS.get(provider)
    if cls is None:
        raise ValueError(
            f"Unsupported LLM_PROVIDER: {provider!r}. Supported values: "
            f"{sorted(_PROVIDERS)} (note: 'local'/Ollama was deliberately rejected for this "
            f"project, see research.md §3a)."
        )
    return cls(**kwargs)
