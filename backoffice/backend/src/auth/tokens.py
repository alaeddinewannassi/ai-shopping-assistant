"""JWT access/refresh tokens (T502).

Access tokens are short-lived (15 min) and carry no server-side state — verified purely by
signature + expiry. Refresh tokens are longer-lived (30 days) and exist only to mint a new
access token via POST /auth/refresh; neither is stored server-side (no revocation list yet —
a real gap: a leaked refresh token is valid until it expires. Revisit if/when this needs to
support "log out everywhere").

No TOTP/MFA here — T502's optional part, not built (see admin-api.yaml's contract note).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

_ACCESS_TOKEN_TTL = timedelta(minutes=15)
_REFRESH_TOKEN_TTL = timedelta(days=30)
_ALGORITHM = "HS256"


class TokenError(Exception):
    """Raised for any invalid/expired/malformed token — callers treat this uniformly as
    "not authenticated," never inspect the specific PyJWT exception type."""


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET is required to issue or verify admin sessions (backoffice/backend/.env.example)."
        )
    return secret


def _encode(admin_user_id: uuid.UUID, token_type: str, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(admin_user_id),
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def create_access_token(admin_user_id: uuid.UUID) -> str:
    return _encode(admin_user_id, "access", _ACCESS_TOKEN_TTL)


def create_refresh_token(admin_user_id: uuid.UUID) -> str:
    return _encode(admin_user_id, "refresh", _REFRESH_TOKEN_TTL)


def decode_token(token: str, *, expected_type: str) -> uuid.UUID:
    """Returns the admin_user_id encoded in a valid, non-expired token of `expected_type`.
    Raises TokenError for anything else — wrong type (e.g. a refresh token presented where
    an access token is required), bad signature, expiry, or malformed payload."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a {expected_type} token")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise TokenError("Malformed token payload") from exc
