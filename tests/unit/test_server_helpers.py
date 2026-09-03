"""The MCP server module without a transport: the structured-error wrapper, the tool registry,
and the CLI options - nothing launches a browser."""

from __future__ import annotations

import pytest

from action_receipt import server as srv
from action_receipt.schema import RECEIPT_JSON_SCHEMA

ACTION_TOOLS = {"open", "navigate", "click", "hover", "select", "type", "press", "scroll"}
OTHER_TOOLS = {"snapshot", "tabs", "receipt_last", "receipt_schema", "receipt_begin", "receipt_end"}
GATE_TOOLS = {"done", "receipt_policy", "browser_info"}


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
    assert set(tools) == ACTION_TOOLS | OTHER_TOOLS | GATE_TOOLS
    for name in ("click", "hover", "select", "type", "press", "scroll"):
        assert "frame" in tools[name].input_schema["properties"], name
    assert tools["click"].input_schema["properties"]["click_count"]["default"] == 1
    assert tools["scroll"].input_schema["properties"]["pages"]["default"] == 1.0
    assert tools["receipt_begin"].input_schema["properties"]["label"]["default"] == "external"
    assert tools["receipt_end"].input_schema.get("properties", {}) == {}
    assert "no_op" in srv.server.instructions and "blocked" in srv.server.instructions
    assert "done" in srv.server.instructions and "receipt_policy" in srv.server.instructions
    done = tools["done"].input_schema
    assert done["required"] == ["summary"] and "claimed_effects" in done["properties"]
    assert tools["receipt_policy"].input_schema.get("properties", {}) == {}


async def test_receipt_schema_tool_returns_the_json_schema():
    assert await srv.receipt_schema() == RECEIPT_JSON_SCHEMA


def test_out_shape():
    class R:
        def to_dict(self):
            return {"id": "x"}

    assert srv._out({"clicked": "#a"}, R()) == {"result": {"clicked": "#a"}, "receipt": {"id": "x"}}


DEFAULT_OPTS = {
    "cdp": None,
    "headless": True,
    "new_context": False,
    "cdp_listen": None,
    "enforce_done": False,
    "max_refusals": 2,
}


@pytest.fixture
def cli(monkeypatch):
    runs: list[str] = []
    monkeypatch.setattr(srv.server, "run", lambda transport: runs.append(transport))
    monkeypatch.setattr(srv, "_opts", dict(DEFAULT_OPTS))
    monkeypatch.setattr(srv, "_done_state", {"refusals": 7})
    for var in ("AR_CDP", "AR_CDP_LISTEN", "AR_ENFORCE_DONE", "AR_MAX_REFUSALS"):
        monkeypatch.delenv(var, raising=False)
    return runs


def test_cli_options_set_the_session_options(cli):
    srv.main([])
    assert srv._opts == DEFAULT_OPTS and cli == ["stdio"]
    assert srv._done_state == {"refusals": 0}  # a fresh process starts with a clean gate
    srv.main(["--cdp", "http://127.0.0.1:9222", "--cdp-new-context", "--headed", "--transport", "sse"])
    assert srv._opts == {
        **DEFAULT_OPTS,
        "cdp": "http://127.0.0.1:9222",
        "headless": False,
        "new_context": True,
    }
    assert cli[-1] == "sse"


def test_cli_gate_and_listen_flags(cli):
    srv.main(["--enforce-done", "--max-refusals", "5", "--cdp-listen", "9333", "--headed"])
    assert srv._opts == {
        "cdp": None,
        "headless": False,
        "new_context": False,
        "cdp_listen": 9333,
        "enforce_done": True,
        "max_refusals": 5,
    }


def test_cli_reads_the_environment_and_flags_win(cli, monkeypatch):
    monkeypatch.setenv("AR_ENFORCE_DONE", "1")
    monkeypatch.setenv("AR_MAX_REFUSALS", "1")
    monkeypatch.setenv("AR_CDP_LISTEN", "9444")
    srv.main([])
    assert srv._opts["enforce_done"] is True and srv._opts["max_refusals"] == 1
    assert srv._opts["cdp_listen"] == 9444
    srv.main(["--max-refusals", "3"])
    assert srv._opts["max_refusals"] == 3 and srv._opts["enforce_done"] is True
    monkeypatch.setenv("AR_ENFORCE_DONE", "0")
    srv.main([])
    assert srv._opts["enforce_done"] is False


def test_cli_rejects_attach_plus_listen_and_negative_caps(cli):
    with pytest.raises(SystemExit):
        srv.main(["--cdp", "http://127.0.0.1:9222", "--cdp-listen", "9222"])
    with pytest.raises(SystemExit):
        srv.main(["--max-refusals", "-1"])
    assert cli == []


async def test_done_and_policy_answer_without_a_browser(monkeypatch):
    """No session means no action was performed; neither tool may launch a browser to say so."""
    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_opts", {**DEFAULT_OPTS, "enforce_done": True})
    monkeypatch.setattr(srv, "_done_state", {"refusals": 0})

    async def never():
        raise AssertionError("done() must not start a browser")

    monkeypatch.setattr(srv, "_sess", never)
    pol = await srv.receipt_policy()
    assert pol == {
        "enforce": True,
        "max_refusals": 2,
        "refusals": 0,
        "actions": 0,
        "last_verdict": None,
        "last_receipt": None,
    }
    out = await srv.done("all set", ["saved the form"])
    assert out["accepted"] is False and out["reason"] == "no action has been performed in this session"
    assert (
        out["refusals"] == 1 and out["summary"] == "all set" and out["claimed_effects"] == ["saved the form"]
    )
    assert out["last_receipt"] is None and out["actions"] == 0
    assert (await srv.receipt_policy())["refusals"] == 1
    # enforcement off: accepted, and the counter is left alone
    srv._opts["enforce_done"] = False
    out = await srv.done("all set")
    assert out["accepted"] is True and out["overridden"] is False and out["enforced"] is False
    assert srv._done_state["refusals"] == 0


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
    monkeypatch.setattr(
        srv, "_opts", {"cdp": "http://x", "headless": False, "new_context": True, "cdp_listen": None}
    )

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
    assert captured["start"] == {
        "cdp": "http://x",
        "headless": False,
        "new_context": True,
        "cdp_listen": None,
    }
    monkeypatch.setattr(srv, "_session", None)
