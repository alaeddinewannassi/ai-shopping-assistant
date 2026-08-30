"""Two tenants in one process never cross-contaminate sessions, carts, or breakers (T208).

Builds two TenantRuntimes the same way src/tenancy/resolver.py does for a real request
(one via legacy_env_tenant_config — the default/unkeyed path, one via a DB-backed Tenant
row with its own TenantAdapterConfig) and drives both through the same DialogueContext-level
API src/api/chat.py's /chat route uses (agent/dialogue.py's handle_turn).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from tenancy_db.base import Base
from tenancy_db.crypto import encrypt_secret, reset_key_cache
from tenancy_db.repositories import TenantAdapterConfigRepository, TenantRepository

from src.agent.dialogue import handle_turn
from src.tenancy.config import legacy_env_tenant_config, load_tenant_config
from src.tenancy.runtime import build_tenant_runtime, clear_all


@pytest.fixture(autouse=True)
def _fresh_runtime_pool():
    clear_all()
    yield
    clear_all()


@pytest.fixture
def db_session(monkeypatch):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "unit-test-key-not-for-production-use-only")
    reset_key_cache()
    engine = sa.create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()
    reset_key_cache()


def test_two_tenants_with_the_same_session_id_have_independent_carts(monkeypatch, db_session) -> None:
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "rule-based-stub")

    # Tenant A: the legacy/default path (no DB row) — mirrors every pre-002 deployment.
    config_a = legacy_env_tenant_config("default")
    runtime_a = build_tenant_runtime(config_a)

    # Tenant B: a real DB-backed tenant, deliberately configured against its own mock store.
    tenants = TenantRepository(db_session)
    tenant_b = tenants.create("store-b", "Store B")
    TenantAdapterConfigRepository(db_session).upsert(
        tenant_b.id, platform="mock", base_url="https://store-b.example/api", api_key_encrypted=encrypt_secret("b-secret")
    )
    db_session.commit()
    config_b = load_tenant_config(db_session, "store-b", by_slug=True)
    runtime_b = build_tenant_runtime(config_b)

    assert runtime_a.adapter is not runtime_b.adapter
    assert runtime_a.dialogue_ctx.pending_gate is not runtime_b.dialogue_ctx.pending_gate

    shared_session_id = "shared-across-tenants"

    # Tenant A adds a product to cart and confirms it.
    reply_a = handle_turn(runtime_a.dialogue_ctx, shared_session_id, "add the red classic t-shirt to my cart")
    assert "confirm" in reply_a.lower()
    confirm_a = handle_turn(runtime_a.dialogue_ctx, shared_session_id, "yes")
    assert "t-shirt" in confirm_a.lower()

    # Tenant B, same session_id string: must see an empty cart, no pending action, and no
    # trace of tenant A's confirmed add.
    session_b = runtime_b.dialogue_ctx.session_store.get_or_create(shared_session_id)
    assert session_b.pending_action is None
    cart_b = runtime_b.adapter.get_cart(session_b.cart_id or shared_session_id)
    assert cart_b.lines == []

    # And tenant A's own session state is untouched by tenant B ever having been queried.
    session_a = runtime_a.dialogue_ctx.session_store.get_or_create(shared_session_id)
    cart_a = runtime_a.adapter.get_cart(session_a.cart_id or shared_session_id)
    assert len(cart_a.lines) == 1


def test_one_tenants_circuit_breaker_does_not_affect_the_other(monkeypatch, db_session) -> None:
    monkeypatch.delenv("PRESTASHOP_BASE_URL", raising=False)
    monkeypatch.delenv("PRESTASHOP_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "rule-based-stub")

    config_a = legacy_env_tenant_config("default")
    runtime_a = build_tenant_runtime(config_a)

    tenants = TenantRepository(db_session)
    tenant_b = tenants.create("store-c", "Store C")
    TenantAdapterConfigRepository(db_session).upsert(
        tenant_b.id, platform="mock", base_url="https://store-c.example/api", api_key_encrypted=encrypt_secret("c-secret")
    )
    db_session.commit()
    config_b = load_tenant_config(db_session, "store-c", by_slug=True)
    runtime_b = build_tenant_runtime(config_b)

    runtime_a.adapter.simulate_outage(True)
    reply = handle_turn(runtime_a.dialogue_ctx, "outage-session", "show me t-shirts")
    assert "can't reach" in reply.lower()

    # Tenant B's adapter was never touched — must behave completely normally.
    reply_b = handle_turn(runtime_b.dialogue_ctx, "outage-session", "show me t-shirts")
    assert "can't reach" not in reply_b.lower()
