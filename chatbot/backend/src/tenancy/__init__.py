"""Multi-tenancy core (specs/002-backoffice-analytics, T201-T204).

Replaces the module-level `_adapter`/`_session_store`/`_dialogue_ctx` singletons
`src/api/chat.py` used to build once at import time. `resolve_tenant_runtime()` is the
FastAPI dependency every request-handling route should depend on; it resolves the request's
tenant (widget key header, or DEFAULT_TENANT_SLUG) and returns a cached `TenantRuntime`
bundling that tenant's adapter, LLM client, session store, and dialogue context.
"""

from src.tenancy.resolver import resolve_tenant_runtime
from src.tenancy.runtime import TenantRuntime

__all__ = ["TenantRuntime", "resolve_tenant_runtime"]
