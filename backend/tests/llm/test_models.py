"""Structured-output parsing and the strict response_format schema."""

import json

import pytest

from app.llm.models import ChatResponse, ResponseParseError, parse_response, response_format


def test_parse_full_response():
    raw = json.dumps(
        {
            "message": "Buying 10 AAPL.",
            "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
            "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
        }
    )
    r = parse_response(raw)
    assert r.message == "Buying 10 AAPL."
    assert r.trades[0].ticker == "AAPL" and r.trades[0].side == "buy"
    assert r.trades[0].quantity == 10
    assert r.watchlist_changes[0].ticker == "PYPL"
    assert r.watchlist_changes[0].action == "add"


def test_parse_message_only_defaults_to_empty_lists():
    r = parse_response('{"message": "Hi"}')
    assert r.trades == [] and r.watchlist_changes == []


def test_parse_null_lists():
    r = parse_response('{"message": "Hi", "trades": null, "watchlist_changes": null}')
    assert r.trades == [] and r.watchlist_changes == []


def test_parse_normalises_case_and_whitespace():
    r = parse_response(
        '{"message": " ok ", "trades": [{"ticker": " tsla ", "side": "SELL", "quantity": 2.5}],'
        ' "watchlist_changes": [{"ticker": "nflx", "action": "Remove"}]}'
    )
    assert r.message == "ok"
    assert (r.trades[0].ticker, r.trades[0].side, r.trades[0].quantity) == ("TSLA", "sell", 2.5)
    assert (r.watchlist_changes[0].ticker, r.watchlist_changes[0].action) == ("NFLX", "remove")


def test_parse_strips_code_fences():
    r = parse_response('```json\n{"message": "fenced"}\n```')
    assert r.message == "fenced"


def test_parse_drops_malformed_items_keeps_valid_ones():
    raw = json.dumps(
        {
            "message": "partial",
            "trades": [
                {"ticker": "AAPL", "side": "hold", "quantity": 1},
                {"ticker": "AAPL", "quantity": 1},
                {"ticker": "MSFT", "side": "buy", "quantity": "lots"},
                {"ticker": "GOOGL", "side": "buy", "quantity": 3},
            ],
            "watchlist_changes": [{"ticker": "X", "action": "watch"}, "PYPL"],
        }
    )
    r = parse_response(raw)
    assert [t.ticker for t in r.trades] == ["GOOGL"]
    assert r.watchlist_changes == []


def test_parse_non_list_actions_ignored():
    r = parse_response('{"message": "x", "trades": {"ticker": "AAPL"}}')
    assert r.trades == []


def test_parse_keeps_nonpositive_quantity_for_execution_layer_to_reject():
    r = parse_response(
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 0}]}'
    )
    assert r.trades[0].quantity == 0


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not json at all",
        '{"message": "unterminated',
        "[1, 2, 3]",
        '"just a string"',
        '{"trades": []}',
        '{"message": ""}',
        '{"message": 42}',
    ],
)
def test_parse_malformed_raises(raw):
    with pytest.raises(ResponseParseError):
        parse_response(raw)


def test_response_format_is_strict_json_schema():
    fmt = response_format()
    assert fmt["type"] == "json_schema"
    js = fmt["json_schema"]
    assert js["strict"] is True
    schema = js["schema"]
    assert set(schema["required"]) == {"message", "trades", "watchlist_changes"}
    assert schema["additionalProperties"] is False
    for definition in schema["$defs"].values():
        assert set(definition["required"]) == set(definition["properties"])
        assert definition["additionalProperties"] is False
    assert '"default"' not in json.dumps(schema)
    # The model's own schema is not mutated by the tightening.
    assert ChatResponse.model_json_schema()["required"] == ["message"]
