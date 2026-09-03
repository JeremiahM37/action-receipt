"""Receipts beside ``@playwright/mcp``: one Chromium (the test's ``own_chromium``), playwright-mcp
attached with ``--cdp-endpoint``, action-receipt attached with ``--cdp``; every playwright-mcp
action is bracketed by ``receipt_begin`` / ``receipt_end`` and the receipt describes what it did.

Needs ``npx`` and the npm registry (the package is pinned so the fetch is cache-friendly and the
tool surface stable); skips itself, never fails, when either is missing."""

from __future__ import annotations

import re
import shutil
import subprocess
from contextlib import asynccontextmanager

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

from .conftest import call, mcp_client

pytestmark = pytest.mark.slow

PW_MCP_VERSION = "0.0.80"
PW_MCP_SPEC = f"@playwright/mcp@{PW_MCP_VERSION}"
FETCH_TIMEOUT_S = 240


def _unavailable() -> str | None:
    """Why playwright-mcp cannot run here (a skip reason), or None."""
    if not shutil.which("npx"):
        return "npx is not on PATH"
    try:
        p = subprocess.run(
            ["npx", "-y", PW_MCP_SPEC, "--version"],
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"{PW_MCP_SPEC} could not be fetched: {type(e).__name__}: {e}"
    if p.returncode != 0 or PW_MCP_VERSION not in p.stdout:
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        return f"{PW_MCP_SPEC} could not be run (rc={p.returncode}): {' | '.join(tail)}"
    return None


@pytest.fixture(scope="module")
def playwright_mcp():
    reason = _unavailable()
    if reason:
        pytest.skip(reason)
    return PW_MCP_SPEC


@asynccontextmanager
async def pw_mcp_client(cdp_url: str, output_dir: str):
    """An initialised ClientSession talking to playwright-mcp attached to ``cdp_url``."""
    params = StdioServerParameters(
        command="npx",
        args=[
            "-y",
            PW_MCP_SPEC,
            "--cdp-endpoint",
            cdp_url,
            "--output-dir",
            output_dir,
            "--timeout-action",
            "1500",
        ],
        env=dict(get_default_environment()),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as cs:
            init = await cs.initialize()
            assert "playwright" in init.server_info.name.lower(), init.server_info
            yield cs


async def pw_call(cs: ClientSession, tool: str, **args) -> str:
    """playwright-mcp returns markdown text; hand it back whole (its errors are text too)."""
    res = await cs.call_tool(tool, args)
    return "\n".join(c.text for c in res.content if getattr(c, "text", None))


def ref_of(snapshot: str, role_and_name: str) -> str:
    m = re.search(
        re.escape(role_and_name) + r"[^\n]*\[ref=([a-z0-9]+)\]", snapshot
    )  # e4, or f1e4 after a navigation
    assert m, f"{role_and_name!r} not in snapshot:\n{snapshot}"
    return m.group(1)


async def test_playwright_mcp_actions_get_receipts(own_chromium, base_url, playwright_mcp, tmp_path):
    async with pw_mcp_client(own_chromium, str(tmp_path)) as pw, mcp_client("--cdp", own_chromium) as ar:
        # playwright-mcp opens the page in the shared default context; action-receipt sees the tab
        out = await pw_call(pw, "browser_navigate", url=f"{base_url}/buttons.html")
        assert "### Error" not in out, out
        tabs = (await call(ar, "tabs"))["tabs"]
        assert any(t["url"].endswith("/buttons.html") for t in tabs), tabs

        # 1. a click that changes the page
        snap = await pw_call(pw, "browser_snapshot")
        ref = ref_of(snap, 'button "Increment"')
        begin = await call(
            ar,
            "receipt_begin",
            label="playwright-mcp:browser_click",
            page_url="buttons.html",
            selector="#counter-btn",
        )
        assert begin["observing"].endswith("/buttons.html")
        out = await pw_call(pw, "browser_click", element="Increment button", target=ref)
        assert "### Error" not in out, out
        rc = (await call(ar, "receipt_end"))["receipt"]
        assert rc["action"]["name"] == "playwright-mcp:browser_click" and rc["dispatch"]["mode"] == "wrap"
        assert rc["verdict"] == "changed", rc["evidence"]
        assert any(
            m["path"].endswith("span#count") and m["after"].endswith("|t=1")
            for m in rc["delta"]["nodes_modified"]
        ), rc["delta"]["nodes_modified"]

        # 2. a click playwright-mcp cannot deliver (disabled): its own error text says timeout;
        #    the receipt says blocked and names the target
        ref = ref_of(snap, 'button "Disabled"')
        await call(
            ar,
            "receipt_begin",
            label="playwright-mcp:browser_click",
            page_url="buttons.html",
            selector="#disabled-btn",
        )
        out = await pw_call(pw, "browser_click", element="Disabled button", target=ref)
        assert "Error" in out or "Timeout" in out, out
        rc = (await call(ar, "receipt_end"))["receipt"]
        assert rc["verdict"] == "blocked" and rc["evidence"][0].startswith("target_disabled"), rc["evidence"]

        # 3. typing on another page: the value delta is attributed to the wrapped action
        out = await pw_call(pw, "browser_navigate", url=f"{base_url}/form.html")
        assert "### Error" not in out, out
        snap = await pw_call(pw, "browser_snapshot")
        ref = ref_of(snap, 'textbox "Name"')
        await call(
            ar, "receipt_begin", label="playwright-mcp:browser_type", page_url="form.html", selector="#name"
        )
        out = await pw_call(pw, "browser_type", element="Name field", target=ref, text="Ada")
        assert "### Error" not in out, out
        rc = (await call(ar, "receipt_end"))["receipt"]
        assert rc["verdict"] == "changed" and rc["delta"]["value_after"] == "Ada", rc["evidence"]

        # 4. nothing between begin and end: the honest no_op
        await call(ar, "receipt_begin", label="playwright-mcp:idle", page_url="form.html")
        rc = (await call(ar, "receipt_end"))["receipt"]
        assert rc["verdict"] == "no_op"
