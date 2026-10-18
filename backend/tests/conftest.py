"""Pytest configuration and fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated environment: tmp DB, no static dir, deterministic simulator, mock LLM."""
    db_path = tmp_path / "db" / "finally.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "no-static"))
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    monkeypatch.setenv("POLYGON_API_KEY", "")
    monkeypatch.setenv("SIM_SEED", "42")
    monkeypatch.setenv("SIM_EVENT_PROBABILITY", "0")
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("ENABLE_RESET", "false")
    return db_path


@pytest.fixture
def make_client(app_env: Path) -> Iterator[Callable[[], TestClient]]:
    """Factory for started TestClients (lifespan runs). Env changes must happen before calling."""
    from app.main import create_app

    clients: list[TestClient] = []

    def _make() -> TestClient:
        client = TestClient(create_app(snapshot_interval=3600))
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in reversed(clients):
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[[], TestClient]) -> TestClient:
    return make_client()
