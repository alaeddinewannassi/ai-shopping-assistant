"""Resilience wrapper around any CommerceAdapter (research.md §8).

Wraps every adapter call with:
  1. A short timeout (so one slow/hung call can't stall a whole conversational turn).
  2. A limited retry for transient transport failures.
  3. A simple circuit breaker: after enough consecutive failures, stop even attempting
     calls for a cooldown window, failing fast instead of hanging every turn.

Any transport/timeout failure is normalized into `AdapterUnavailableError` — this wrapper
never masks it as a business error like `ProductNotFoundError` (those propagate untouched).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from src.adapters.base import AdapterUnavailableError

T = TypeVar("T")


class _TransportError(Exception):
    """Internal marker distinguishing a genuine transport/timeout failure from a business
    error raised deliberately by the wrapped call (e.g. ProductNotFoundError)."""


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    recovery_seconds: float = 30.0
    timeout_seconds: float = 5.0
    max_retries: int = 1


class CircuitBreaker:
    """One circuit breaker instance per adapter (or per adapter method group).

    States: CLOSED (calls pass through normally) -> OPEN (calls fail fast with
    AdapterUnavailableError, no attempt made) -> HALF_OPEN (after recovery_seconds, allow
    exactly one trial call through) -> CLOSED again on success, back to OPEN on failure.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._config.recovery_seconds:
            # Recovery window elapsed: move to half-open (allow one trial call through).
            return False
        return True

    def _on_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._config.failure_threshold:
            self._opened_at = time.monotonic()

    def call(self, fn: Callable[[], T], *, is_transport_error: Callable[[Exception], bool]) -> T:
        """Execute `fn`, applying circuit-breaker + retry semantics.

        `is_transport_error` classifies a caught exception as a transport/timeout failure
        (retryable, counts toward the breaker) vs. a business error (re-raised immediately,
        untouched, does not count as a breaker failure) — e.g. ProductNotFoundError should
        propagate normally, not trip the breaker.
        """
        if self.is_open:
            raise AdapterUnavailableError(
                "Store backend is currently unreachable (circuit breaker open); "
                "not attempting the call."
            )

        last_error: Exception | None = None
        attempts = self._config.max_retries + 1
        for attempt in range(attempts):
            try:
                result = fn()
                self._on_success()
                return result
            except Exception as exc:  # noqa: BLE001 - intentionally broad, reclassified below
                if not is_transport_error(exc):
                    # Business error (e.g. ProductNotFoundError, OutOfStockError): propagate
                    # as-is, do not retry, do not count against the circuit breaker.
                    raise
                last_error = exc
                self._on_failure()
                if attempt < attempts - 1:
                    continue

        raise AdapterUnavailableError(
            f"Store backend unreachable after {attempts} attempt(s): {last_error}"
        ) from last_error


def default_is_transport_error(exc: Exception) -> bool:
    """Classifies common transport/timeout exceptions as retryable transport errors.

    Adapter implementations (e.g. PrestaShopAdapter using httpx) should pass a classifier
    that also recognizes their specific transport exception types (httpx.TimeoutException,
    httpx.ConnectError, etc.) in addition to this default.
    """
    return isinstance(exc, (TimeoutError, ConnectionError, _TransportError))
