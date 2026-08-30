"""TenantConfig must never leak decrypted secrets via its default repr (T705 security
review) — a real risk since src/tenancy/config.py holds api keys in plaintext in memory,
and a plain dataclass repr would otherwise include them in any traceback or stray log line.
"""

from __future__ import annotations

import uuid

from src.tenancy.config import TenantConfig


def test_repr_excludes_adapter_and_llm_api_keys() -> None:
    config = TenantConfig(
        tenant_id=uuid.uuid4(),
        slug="store-a",
        name="Store A",
        adapter_api_key="super-secret-webservice-key",
        llm_api_key="super-secret-llm-key",
    )
    rendered = repr(config)
    assert "super-secret-webservice-key" not in rendered
    assert "super-secret-llm-key" not in rendered
    # Non-secret fields still show up — this isn't a blanket repr suppression.
    assert "store-a" in rendered
