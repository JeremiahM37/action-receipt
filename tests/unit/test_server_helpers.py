"""The MCP server module without a transport: the structured-error wrapper, the tool registry,
and the CLI options - nothing launches a browser."""

from __future__ import annotations

import pytest

from action_receipt import server as srv
from action_receipt.schema import RECEIPT_JSON_SCHEMA

ACTION_TOOLS = {"open", "navigate", "click", "hover", "select", "type", "press", "scroll"}
OTHER_TOOLS = {"snapshot", "tabs", "receipt_last", "receipt_schema", "receipt_begin", "receipt_end"}


async def test_structured_wrapper_turns_exceptions_into_results():
    @srv._structured
    async def boom(x):
        raise RuntimeError("first line\nsecond line")

    out = await boom(1)
    assert out == {"error": {"type": "RuntimeError", "message": "first line"}, "receipt": None}


async def test_structured_wrapper_truncates_and_passes_results_through():
    @srv._structured
    async def long_error():
        raise ValueError("x" * 1000)

    assert len((await long_error())["error"]["message"]) == 300

    @srv._structured
    async def ok(a, b=2):
        return {"sum": a + b}

    assert await ok(1, b=3) == {"sum": 4}
    assert ok.__name__ == "ok"


async def test_structured_wrapper_names_the_class_even_though_type_is_shadowed():
    # `type` is a tool in this module; the wrapper must still report the exception class.
    assert callable(srv.type)

    @srv._structured
    async def kaboom():
        raise KeyError("k")

    assert (await kaboom())["error"]["type"] == "KeyError"


async def test_every_tool_is_registered_with_the_documented_signature():
    tools = {t.name: t for t in await srv.server.list_tools()}
    assert set(tools) == ACTION_TOOLS | OTHER_TOOLS
    for name in ("click", "hover", "select", "type", "press", "scroll"):
        assert "frame" in tools[name].input_schema["properties"], name
    assert tools["click"].input_schema["properties"]["click_count"]["default"] == 1
    assert tools["scroll"].input_schema["properties"]["pages"]["default"] == 1.0
    assert tools["receipt_begin"].input_schema["properties"]["label"]["default"] == "external"
    assert tools["receipt_end"].input_schema.get("properties", {}) == {}
    assert "no_op" in srv.server.instructions and "blocked" in srv.server.instructions


async def test_receipt_schema_tool_returns_the_json_schema():
    assert await srv.receipt_schema() == RECEIPT_JSON_SCHEMA


def test_out_shape():
    class R:
        def to_dict(self):
            return {"id": "x"}

    assert srv._out({"clicked": "#a"}, R()) == {"result": {"clicked": "#a"}, "receipt": {"id": "x"}}


def test_cli_options_set_the_session_options(monkeypatch):
    runs: list[str] = []
    monkeypatch.setattr(srv.server, "run", lambda transport: runs.append(transport))
    monkeypatch.setattr(srv, "_opts", {"cdp": None, "headless": True, "new_context": False})
    monkeypatch.delenv("AR_CDP", raising=False)
    srv.main([])
    assert srv._opts == {"cdp": None, "headless": True, "new_context": False} and runs == ["stdio"]
    srv.main(["--cdp", "http://127.0.0.1:9222", "--cdp-new-context", "--headed", "--transport", "sse"])
    assert srv._opts == {"cdp": "http://127.0.0.1:9222", "headless": False, "new_context": True}
    assert runs[-1] == "sse"


def test_cli_reads_the_cdp_endpoint_from_the_environment(monkeypatch):
    monkeypatch.setattr(srv.server, "run", lambda transport: None)
    monkeypatch.setattr(srv, "_opts", {"cdp": None, "headless": True, "new_context": False})
    monkeypatch.setenv("AR_CDP", "ws://127.0.0.1:9222/devtools/browser/abc")
    srv.main([])
    assert srv._opts["cdp"] == "ws://127.0.0.1:9222/devtools/browser/abc"


def test_cli_rejects_an_unknown_transport(monkeypatch):
    monkeypatch.setattr(srv.server, "run", lambda transport: None)
    with pytest.raises(SystemExit):
        srv.main(["--transport", "carrier-pigeon"])


def test_session_settings_come_from_the_environment(monkeypatch):
    """_sess() builds the SettleConfig from AR_* variables; check the mapping without starting a browser."""
    monkeypatch.setenv("AR_QUIET_MS", "250")
    monkeypatch.setenv("AR_SETTLE_TIMEOUT_MS", "4000")
    monkeypatch.setenv("AR_BODY_GRACE_MS", "10")
    monkeypatch.setenv("AR_TIMER_WAIT_MS", "0")
    monkeypatch.setenv("AR_PRE_SETTLE_MS", "5")
    captured = {}

    class FakeSession:
        def __init__(self, *, settle_cfg):
            captured["cfg"] = settle_cfg

        async def start(self, **kw):
            captured["start"] = kw

    monkeypatch.setattr(srv, "ReceiptSession", FakeSession)
    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_opts", {"cdp": "http://x", "headless": False, "new_context": True})

    import asyncio

    s = asyncio.run(srv._sess())
    assert isinstance(s, FakeSession)
    cfg = captured["cfg"]
    assert (cfg.quiet_ms, cfg.timeout_ms, cfg.body_grace_ms, cfg.timer_wait_ms, cfg.pre_settle_ms) == (
        250.0,
        4000.0,
        10.0,
        0.0,
        5.0,
    )
    assert captured["start"] == {"cdp": "http://x", "headless": False, "new_context": True}
    monkeypatch.setattr(srv, "_session", None)
