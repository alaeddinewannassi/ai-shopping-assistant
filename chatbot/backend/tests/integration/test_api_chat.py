"""Integration tests for the FastAPI HTTP surface (T016, T065).

Unlike the other integration suites (which call agent/dialogue.py's handle_turn directly),
this exercises api/chat.py's actual module-level wiring end-to-end via TestClient — the
same object graph POST /chat and GET /health run against in production.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.chat import app

client = TestClient(app)


def test_health_reports_ok_with_default_mock_adapter_and_no_redis_configured() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["adapter"] == "ok"  # MockAdapter is always reachable
    assert body["redis"] == "not_configured"  # no REDIS_URL set in the test environment
    assert body["status"] == "ok"
    assert body["dropped_events"] >= 0  # T703: the analytics writer's overflow counter


def test_chat_endpoint_routes_a_discovery_turn_end_to_end() -> None:
    resp = client.post("/chat", json={"session_id": "http-test-1", "message": "show me t-shirts"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "http-test-1"
    assert "T-Shirt" in body["reply"] or "shirt" in body["reply"].lower()
    assert body["needs_confirmation"] is False  # a plain search never proposes anything
    assert body["product_links"] == [{"id": "prod-tshirt-1", "name": "Classic T-Shirt"}]
    assert body["show_cart_link"] is False


def test_chat_endpoint_routes_a_full_add_to_cart_confirmation_flow() -> None:
    session_id = "http-test-2"
    propose = client.post(
        "/chat", json={"session_id": session_id, "message": "add the red classic t-shirt to my cart"}
    )
    assert "confirm" in propose.json()["reply"].lower()
    # Structural, not text-matched — set from whether a PendingAction genuinely exists
    # (session.pending_action), independent of how the reply happens to be phrased.
    assert propose.json()["needs_confirmation"] is True
    assert propose.json()["show_cart_link"] is True

    confirm = client.post("/chat", json={"session_id": session_id, "message": "yes"})
    assert "t-shirt" in confirm.json()["reply"].lower()
    assert confirm.json()["needs_confirmation"] is False  # spent — nothing left pending
    assert confirm.json()["show_cart_link"] is True
