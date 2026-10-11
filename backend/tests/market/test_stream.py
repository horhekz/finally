"""Tests for the SSE stream (design doc §12)."""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.market import PriceCache, create_stream_router
from app.market import stream as stream_mod
from app.market.stream import format_prices_event, generate_price_events


class FakeRequest:
    client = None

    def __init__(self):
        self.disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def parse_data(frame: str) -> dict:
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    return json.loads(frame[len("data: ") :])


async def test_stream_events():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    req = FakeRequest()
    gen = generate_price_events(cache, req, interval=0.01)
    assert await anext(gen) == "retry: 1000\n\n"
    payload = parse_data(await anext(gen))
    assert payload["AAPL"]["price"] == 190.0 and payload["AAPL"]["direction"] == "flat"
    cache.remove("AAPL")
    assert parse_data(await anext(gen)) == {}  # empty payload after the last removal
    req.disconnected = True
    await asyncio.sleep(0)
    with pytest.raises(StopAsyncIteration):
        await anext(gen)


async def test_stream_only_sends_on_change():
    cache = PriceCache()
    cache.update("AAPL", 190.0, reference_price=190.0)
    req = FakeRequest()
    gen = generate_price_events(cache, req, interval=0.01)
    await anext(gen)  # retry
    await anext(gen)  # initial snapshot
    nxt = asyncio.ensure_future(anext(gen))
    await asyncio.sleep(0.05)
    assert not nxt.done()  # nothing changed → nothing sent
    cache.update("AAPL", 191.9)
    payload = parse_data(await asyncio.wait_for(nxt, 1))
    assert payload["AAPL"]["direction"] == "up"
    assert payload["AAPL"]["day_change_percent"] == 1.0
    req.disconnected = True
    await gen.aclose()


async def test_stream_heartbeat(monkeypatch):
    monkeypatch.setattr(stream_mod, "HEARTBEAT_EVERY", 0.03)
    cache = PriceCache()
    req = FakeRequest()
    gen = generate_price_events(cache, req, interval=0.01)
    assert await anext(gen) == "retry: 1000\n\n"
    assert parse_data(await anext(gen)) == {}  # empty cache still sends one snapshot
    assert await asyncio.wait_for(anext(gen), 1) == ": ping\n\n"
    req.disconnected = True
    await gen.aclose()


def test_format_prices_event_contains_wire_fields():
    cache = PriceCache()
    cache.update("AAPL", 190.0, timestamp=1.0, reference_price=190.0)
    payload = parse_data(format_prices_event(cache))
    assert set(payload["AAPL"]) == {
        "ticker", "price", "previous_price", "timestamp", "change", "change_percent",
        "direction", "reference_price", "day_change", "day_change_percent",
    }


def test_router_factory_can_be_called_twice():
    cache = PriceCache()
    app = FastAPI()
    app.include_router(create_stream_router(cache))
    assert len(create_stream_router(cache).routes) == 1
    assert [r.path for r in app.routes].count("/api/stream/prices") == 1


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sse_endpoint_over_http():
    """End-to-end: real uvicorn server, real HTTP client, real SSE frames."""
    cache = PriceCache()
    cache.update("AAPL", 190.0, reference_price=190.0)
    app = FastAPI()
    app.include_router(create_stream_router(cache, interval=0.05))

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", ws="none")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started

        url = f"http://127.0.0.1:{port}/api/stream/prices"
        with httpx.stream("GET", url, timeout=5) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            frames: list[str] = []
            buf = ""
            for chunk in resp.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    frame, buf = buf.split("\n\n", 1)
                    frames.append(frame)
                    if len(frames) == 2:
                        cache.update("AAPL", 191.0)  # after the first snapshot: trigger another
                if len(frames) >= 3:
                    break
        assert frames[0] == "retry: 1000"
        first = json.loads(frames[1][len("data: ") :])
        second = json.loads(frames[2][len("data: ") :])
        assert first["AAPL"]["price"] == 190.0
        assert second["AAPL"]["price"] == 191.0 and second["AAPL"]["direction"] == "up"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
