"""Structured JSON audit logging (FR-014, T015).

Every navigation change, cart mutation, promo suggestion/application, and checkout action
is logged with enough detail to reconstruct the assistant's decisions after the fact.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Optional

_logger = logging.getLogger("assistant.audit")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


def log_action(
    session_id: str,
    intent: str,
    action: str,
    outcome: str,
    *,
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Emits one structured JSON audit line.

    Args:
        session_id: which ConversationSession this event belongs to.
        intent: the triggering natural-language intent/action_type (e.g. "propose_add_to_cart").
        action: the concrete action taken (e.g. "add_cart_item(variant=var-tshirt-1-red-m)").
        outcome: "success" | "declined" | "error" | "unavailable" | ... — the result.
        details: any additional structured context (adapter result summary, error message).
    """
    record = {
        "timestamp": time.time(),
        "session_id": session_id,
        "intent": intent,
        "action": action,
        "outcome": outcome,
        "details": details or {},
    }
    _logger.info(json.dumps(record, default=str))
