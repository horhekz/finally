"""HTTP layer for /api/chat with faked services (mock LLM mode)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat import router
from app.llm import service


@pytest.fixture
def client(mock_mode, fake_db, fake_portfolio):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_post_chat_returns_assistant_message(client):
    resp = client.post("/api/chat", json={"message": "buy"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "assistant"
    assert body["content"] == "Mock: buying 1 share of AAPL."
    assert body["actions"]["trades"][0]["status"] == "executed"
    assert body["actions"]["watchlist_changes"] == []
    assert body["id"] and body["created_at"]


@pytest.mark.parametrize("payload", [{"message": ""}, {"message": "   "}, {}, {"message": 5}])
def test_post_chat_rejects_empty(client, payload):
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code == 400
    assert resp.json() == {"error": "Message must not be empty"}


def test_post_chat_rejects_too_long(client):
    resp = client.post("/api/chat", json={"message": "x" * 4001})
    assert resp.status_code == 400
    assert "too long" in resp.json()["error"]


def test_post_chat_never_500(client, monkeypatch, fake_db):
    def broken(role, content, actions):
        raise RuntimeError("disk full")

    monkeypatch.setattr(fake_db, "insert_chat_message", broken)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == service.GENERIC_ERROR_MESSAGE
    assert body["actions"] == {"trades": [], "watchlist_changes": []}


def test_history_endpoint(client):
    client.post("/api/chat", json={"message": "hello"})
    resp = client.get("/api/chat/history")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["actions"] is None
    assert messages[1]["actions"] == {"trades": [], "watchlist_changes": []}
