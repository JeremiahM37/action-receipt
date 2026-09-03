"""Drive playwright-mcp and action-receipt side by side over stdio and print the receipts.

    python examples/playwright-mcp/drive_both.py [--headed] [--port 9222]

What it does: action-receipt is started with ``--cdp-listen PORT`` (it launches the Chromium and
publishes the remote-debugging port), playwright-mcp with ``--cdp-endpoint http://127.0.0.1:PORT``
(it attaches to the same browser). A scripted three-step task on the fixture page next to this
file — type a name, pick a size, click Save — is performed with playwright-mcp's tools, each
call bracketed by ``receipt_begin`` / ``receipt_end``, and the receipt for each step is printed.
Step 3 is run twice on purpose: once while Save is still disabled (the receipt says ``blocked``
and why) and once after the name is in (``changed``, with the status text in the delta).
The task ends with ``done``; action-receipt runs with ``--enforce-done``, so the first ``done``
(made right after the blocked click) is refused and the second is accepted.

Needs: ``pip install action-receipt`` (this repo), ``npx`` (Node ≥ 18). The playwright-mcp
package is fetched by npx on first run and pinned here.
"""

from __future__ import annotations

import argparse
import asyncio
import http.server
import re
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

HERE = Path(__file__).resolve().parent
PW_MCP_SPEC = "@playwright/mcp@0.0.80"


def serve_fixture() -> str:
    """The fixture page on a local port (playwright-mcp refuses file:// URLs by default)."""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE), **kw)

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/fixture.html"


def text_of(res) -> str:
    return "\n".join(c.text for c in res.content if getattr(c, "text", None))


def ref_of(snapshot: str, role_and_name: str) -> str:
    m = re.search(re.escape(role_and_name) + r"[^\n]*\[ref=([a-z0-9]+)\]", snapshot)
    if not m:
        sys.exit(f"{role_and_name!r} not found in the playwright-mcp snapshot:\n{snapshot}")
    return m.group(1)


def show(step: str, receipt: dict) -> None:
    d = receipt["delta"]
    print(f"\n== {step}")
    print(f"   verdict:    {receipt['verdict']}")
    print(f"   evidence:   {receipt['evidence'][:4]}")
    print(f"   hint:       {receipt['hint']}")
    s = receipt["settlement"]
    print(f"   settlement: settled_by={s['settled_by']} elapsed={s['elapsed_ms']}ms")
    if d["value_changed"]:
        print(f"   value:      {d['value_before']!r} -> {d['value_after']!r}")
    for n in d["nodes_modified"][:3]:
        print(f"   modified:   {n['path']}: {n['before'][:60]!r} -> {n['after'][:60]!r}")


async def main(port: int, headed: bool) -> None:
    url = serve_fixture()
    cdp = f"http://127.0.0.1:{port}"
    env = dict(get_default_environment())
    ar = StdioServerParameters(
        command="action-receipt",
        args=["--cdp-listen", str(port), "--enforce-done", *(["--headed"] if headed else [])],
        env=env,
    )
    pw = StdioServerParameters(
        command="npx",
        args=["-y", PW_MCP_SPEC, "--cdp-endpoint", cdp, "--output-dir", tempfile.mkdtemp(prefix="pw-mcp-")],
        env=env,
    )
    # action-receipt first: with --cdp-listen the browser is up once initialize() returns
    async with stdio_client(ar) as (r1, w1), ClientSession(r1, w1) as receipt:
        await receipt.initialize()
        info = (await receipt.call_tool("browser_info", {})).structured_content
        print(f"action-receipt: mode={info['mode']} cdp_url={info['cdp_url']}")
        async with stdio_client(pw) as (r2, w2), ClientSession(r2, w2) as play:
            init = await play.initialize()
            print(f"playwright-mcp: {init.server_info.name} {init.server_info.version}")

            async def wrapped(label: str, selector: str | None, tool: str, args: dict) -> dict:
                await receipt.call_tool(
                    "receipt_begin", {"label": label, "selector": selector, "page_url": "fixture"}
                )
                out = text_of(await play.call_tool(tool, args))
                rc = (await receipt.call_tool("receipt_end", {})).structured_content["receipt"]
                show(f"{label}  ({tool} said: {out.splitlines()[0] if out else '-'})", rc)
                return rc

            print(text_of(await play.call_tool("browser_navigate", {"url": url})).splitlines()[0])
            snap = text_of(await play.call_tool("browser_snapshot", {}))

            # Step 3 too early: Save is disabled until a name is typed. playwright-mcp times out;
            # the receipt says blocked and names the target.
            rc = await wrapped(
                "click Save (too early)",
                "#save",
                "browser_click",
                {"element": "Save", "target": ref_of(snap, 'button "Save"')},
            )
            assert rc["verdict"] == "blocked", rc["verdict"]

            # An agent claiming done here is refused: the last receipt was blocked.
            d = (await receipt.call_tool("done", {"summary": "saved the order"})).structured_content
            print(f"\n== done -> accepted={d['accepted']} reason={d['reason']!r}")
            assert d["accepted"] is False

            # Steps 1-3 properly
            rc = await wrapped(
                "type name",
                "#name",
                "browser_type",
                {"element": "Name", "target": ref_of(snap, 'textbox "Name"'), "text": "Ada"},
            )
            assert rc["verdict"] == "changed" and rc["delta"]["value_after"] == "Ada"
            rc = await wrapped(
                "select size",
                "#size",
                "browser_select_option",
                {"element": "Size", "target": ref_of(snap, 'combobox "Size"'), "values": ["l"]},
            )
            assert rc["verdict"] == "changed"
            rc = await wrapped(
                "click Save",
                "#save",
                "browser_click",
                {"element": "Save", "target": ref_of(snap, 'button "Save"')},
            )
            assert rc["verdict"] == "changed"
            assert any("saved Ada / l" in n["after"] for n in rc["delta"]["nodes_modified"]), rc["delta"][
                "nodes_modified"
            ]

            d = (
                await receipt.call_tool("done", {"summary": "saved the order for Ada, size L"})
            ).structured_content
            print(
                f"\n== done -> accepted={d['accepted']} overridden={d['overridden']} refusals={d['refusals']}"
            )
            assert d["accepted"] is True and d["overridden"] is False
    print(
        "\nall three steps have receipts; done was refused once (after the blocked click) and then accepted"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.port, a.headed))
