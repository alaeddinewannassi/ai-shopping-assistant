"""Public widget key generation (T507).

Keys are public by design (plan.md D2 — they ship in browser JS, so they can't be secret);
abuse control is the origin allowlist, not secrecy. `pk_live_` prefix makes a key
recognizable at a glance (mirroring Stripe-style prefixed public keys).
"""

from __future__ import annotations

import secrets


def generate_public_key() -> str:
    return f"pk_live_{secrets.token_urlsafe(24)}"
