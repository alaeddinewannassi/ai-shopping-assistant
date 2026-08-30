"""Smoke test for the backoffice API skeleton (mirrors chatbot/backend's own /health test
style, tests/integration/test_api_chat.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_health_reports_not_configured_with_no_database_url(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from tenancy_db import engine as db_engine

    db_engine.reset_engine()

    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"] == "not_configured"
    assert body["status"] == "degraded"
