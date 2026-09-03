"""Drive the real MCP server over stdio with the `mcp` client: list tools, open + scroll + click,
check the structured output, and check that failures come back structured rather than as
protocol errors."""

from __future__ import annotations

import sys

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

from action_receipt.schema import validate_receipt

ACTION_TOOLS = {"open", "navigate", "click", "hover", "select", "type", "press", "scroll"}
OTHER_TOOLS = {"snapshot", "tabs", "receipt_last", "receipt_schema", "receipt_begin", "receipt_end"}


def _params() -> StdioServerParameters:
    env = dict(get_default_environment())
    env["AR_SETTLE_TIMEOUT_MS"] = "8000"
    env["AR_QUIET_MS"] = "100"
    return StdioServerParameters(command=sys.executable, args=["-m", "action_receipt.server"], env=env)


async def test_mcp_stdio_round_trip(base_url):
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as cs:
            init = await cs.initialize()
            assert init.server_info.name == "action-receipt"

            tools = await cs.list_tools()
            by_name = {t.name: t for t in tools.tools}
            assert set(by_name) >= ACTION_TOOLS | OTHER_TOOLS, set(by_name)
            for name in ("click", "hover", "select", "type", "press", "scroll"):
                assert "frame" in by_name[name].input_schema["properties"], name
            assert by_name["click"].input_schema["properties"]["click_count"]["default"] == 1

            res = await cs.call_tool("open", {"url": f"{base_url}/long.html"})
            assert not res.is_error, res
            out = res.structured_content
            assert out["result"]["status"] == 200
            rc = out["receipt"]
            validate_receipt(rc)
            assert rc["verdict"] == "navigated" and rc["after"]["url"].endswith("/long.html")

            res = await cs.call_tool("scroll", {"direction": "down", "pages": 1.0})
            rc = res.structured_content["receipt"]
            validate_receipt(rc)
            assert rc["verdict"] == "changed" and rc["delta"]["scroll_dy"] > 0
            assert res.structured_content["result"]["wheel"]["dy"] > 0

            res = await cs.call_tool("navigate", {"url": f"{base_url}/buttons.html"})
            assert res.structured_content["receipt"]["verdict"] == "navigated"

            res = await cs.call_tool("click", {"selector": "#counter-btn"})
            rc = res.structured_content["receipt"]
            validate_receipt(rc)
            assert rc["verdict"] == "changed"
            assert any(
                m["path"].endswith("span#count") and m["after"].endswith("|t=1")
                for m in rc["delta"]["nodes_modified"]
            )

            res = await cs.call_tool("click", {"selector": "#disabled-btn"})
            rc = res.structured_content["receipt"]
            validate_receipt(rc)
            assert rc["verdict"] == "blocked" and rc["hint"]

            res = await cs.call_tool("scroll", {"direction": "down"})
            rc = res.structured_content["receipt"]
            assert rc["verdict"] == "no_op" and "not scrollable" in rc["hint"]

            res = await cs.call_tool("receipt_last", {"n": 2})
            got = res.structured_content["receipts"]
            assert len(got) == 2 and got[-1]["id"] == rc["id"]

            res = await cs.call_tool("snapshot", {"aria": True})
            snap = res.structured_content
            assert snap["url"].endswith("/buttons.html") and "button" in snap["aria"]

            res = await cs.call_tool("tabs", {})
            assert len(res.structured_content["tabs"]) == 1 and res.structured_content["tabs"][0]["current"]

            res = await cs.call_tool("receipt_schema", {})
            assert res.structured_content["title"] == "ReceiptModel"

            # wrap mode over MCP: begin, nothing happens, end
            res = await cs.call_tool("receipt_begin", {"label": "external-noop", "selector": "#counter-btn"})
            assert res.structured_content["receipt_id"]
            res = await cs.call_tool("receipt_end", {})
            rc = res.structured_content["receipt"]
            validate_receipt(rc)
            assert rc["verdict"] == "no_op" and rc["dispatch"]["mode"] == "wrap"


async def test_mcp_failures_are_structured_not_protocol_errors(base_url):
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as cs:
            await cs.initialize()
            # receipt_end without begin
            res = await cs.call_tool("receipt_end", {})
            assert not res.is_error
            assert res.structured_content["receipt"] is None
            assert res.structured_content["error"]["type"] == "RuntimeError"
            # an action before any tab exists
            res = await cs.call_tool("click", {"selector": "#x"})
            assert not res.is_error and res.structured_content["error"]["type"] == "RuntimeError"
            assert "open()" in res.structured_content["error"]["message"]
            # bad tab index
            await cs.call_tool("open", {"url": f"{base_url}/buttons.html"})
            res = await cs.call_tool("tabs", {"select": 7})
            assert (
                res.structured_content["error"]["type"] == "IndexError"
                and res.structured_content["count"] == 1
            )
            # a selector that matches nothing: a receipt, not an exception
            res = await cs.call_tool("click", {"selector": "#does-not-exist"})
            rc = res.structured_content["receipt"]
            validate_receipt(rc)
            assert rc["verdict"] == "unknown" and rc["dispatch"]["ok"] is False
            assert rc["evidence"][0].startswith("dispatch_error")
            # the server is still alive and usable after all that
            res = await cs.call_tool("click", {"selector": "#counter-btn"})
            assert res.structured_content["receipt"]["verdict"] == "changed"
