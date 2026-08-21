"""Declarative PromoStrategy rule config + loader (T056).

Per data-model.md's PromoStrategy entity and contracts/promo-strategy.md: this module only
defines *when/what to suggest*. It never talks to the adapter and never decides validity —
that is exclusively `adapter.validate_promo()`'s job (Constitution Principle VI).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


@dataclass
class PromoStrategyRule:
    rule_id: str
    condition: str
    target_code: str
    priority: int = 0
    stackable_with: list[str] = field(default_factory=list)


DEFAULT_RULES_PATH = Path(__file__).parent / "rules.json"


def load_rules(path: Union[str, Path, None] = None) -> list[PromoStrategyRule]:
    """Loads the PromoStrategy rule set from JSON (default: promo/rules.json,
    the quickstart.md demo strategy: WELCOME10 first-order, BIGCART15 spend threshold)."""
    rules_path = Path(path) if path else DEFAULT_RULES_PATH
    with rules_path.open() as f:
        raw = json.load(f)
    return [PromoStrategyRule(**entry) for entry in raw]
