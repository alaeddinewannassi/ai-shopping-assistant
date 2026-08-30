"""Per-turn context for enriched audit events (T302).

`handle_turn()` opens a TurnContext at the start of each conversational turn and clears it
at the end; every `log_action()` call in between (agent/dialogue.py's ~25 existing call
sites, none of which change) reads it via a contextvar to attach `tenant_id`/`turn_id`/`seq`
without any of those call sites needing to pass that context through explicitly.

A plain `contextvars.ContextVar` (not `threading.local`) because FastAPI's sync routes each
run in their own anyio worker thread, but contextvars are what ASGI actually propagates
per-request — this matches the same propagation FastAPI itself relies on.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class TurnContext:
    tenant_id: uuid.UUID | None
    session_id: str
    turn_id: uuid.UUID = field(default_factory=uuid.uuid4)
    started_at: float = field(default_factory=time.monotonic)
    _next_seq: int = 0

    # Populated by LLMClient.parse_turn() implementations that make a real call (currently
    # only FreeTierHostedLLMClient) via record_llm_usage() — stay None for every turn that
    # doesn't (RuleBasedStubClient never touches these).
    llm_provider: str | None = None
    llm_model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    llm_ms: int | None = None

    def next_seq(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def record_llm_usage(
        self,
        *,
        provider: str,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        llm_ms: int,
    ) -> None:
        """Called once per turn by an LLMClient that made a real network call — never by
        RuleBasedStubClient. `log_turn_completed()` (logging/audit.py) reads these fields
        onto the turn_completed event."""
        self.llm_provider = provider
        self.llm_model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.llm_ms = llm_ms


_current: ContextVar[TurnContext | None] = ContextVar("_current_turn_context", default=None)


@contextmanager
def turn_scope(tenant_id: uuid.UUID | None, session_id: str) -> Iterator[TurnContext]:
    """`with turn_scope(...) as turn: ...` — sets the context for the duration of one
    conversational turn, restoring whatever was there before (None, normally) on exit so a
    stray log_action() call outside a turn never picks up a stale context."""
    ctx = TurnContext(tenant_id=tenant_id, session_id=session_id)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


def current() -> TurnContext | None:
    """The active TurnContext, or None outside any turn_scope()."""
    return _current.get()
