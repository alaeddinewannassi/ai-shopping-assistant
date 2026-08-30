"""Durable storage layer for tenancy + analytics (feature 002-backoffice-analytics).

Redis (src/session/store.py) remains the hot store for in-flight conversation state; this
package owns everything that must outlive a session TTL: tenant configuration, admin
accounts, and the analytics event stream.

Every module here is written so the assistant keeps serving chat turns when the database is
unreachable or simply not configured (`DATABASE_URL` unset) — see `engine.is_configured()`.
"""

from tenancy_db.engine import get_session, is_configured, session_scope

__all__ = ["get_session", "is_configured", "session_scope"]
