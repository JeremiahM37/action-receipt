"""``--cdp-listen PORT``: the server launches its own Chromium with a remote-debugging port and
works in the browser's default context, so a second tool attached to that port shares the same
tabs - the one-line setup for running beside playwright-mcp. The browser is up before the first
tool call (another tool's call may come first) and gone when the server exits."""

from __future__ import annotations

import asyncio
import socket

from playwright.async_api import async_playwright

from tests.conftest import cdp_alive, cdp_targets

from .conftest import call, mcp_client


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_cdp_listen_shares_the_default_context_with_another_client(base_url):
    port = _free_port()
    cdp_url = f"http://127.0.0.1:{port}"
    async with mcp_client("--cdp-listen", str(port)) as cs:
        # the browser was started with the server, not on our first call
        assert cdp_alive(cdp_url), "remote-debugging port not up after initialize()"
        info = await call(cs, "browser_info")
        assert info["mode"] == "listen" and info["cdp_url"] == cdp_url and info["owns_browser"] is True
        assert info["headless"] is True and info["hint"] is None

        # another client (standing in for playwright-mcp --cdp-endpoint) attaches and acts
        async with async_playwright() as pw:
            other = await pw.chromium.connect_over_cdp(cdp_url)
            page = await other.contexts[0].new_page()
            await page.goto(f"{base_url}/buttons.html")
            tabs = (await call(cs, "tabs"))["tabs"]
            assert any(t["url"].endswith("/buttons.html") for t in tabs), tabs
            assert any(t["url"].endswith("/buttons.html") for t in (await call(cs, "browser_info"))["tabs"])

            await call(
                cs,
                "receipt_begin",
                label="other-client-click",
                page_url="buttons.html",
                selector="#counter-btn",
            )
            await page.click("#counter-btn")
            rc = (await call(cs, "receipt_end"))["receipt"]
            assert rc["verdict"] == "changed" and rc["dispatch"]["mode"] == "wrap", rc["evidence"]
            assert any(m["path"].endswith("span#count") for m in rc["delta"]["nodes_modified"])

            # and our own tools act on the same tab the other client opened
            rc = (await call(cs, "click", selector="#counter-btn"))["receipt"]
            assert rc["verdict"] == "changed"
            assert any(m["after"].endswith("|t=2") for m in rc["delta"]["nodes_modified"]), rc["delta"]
            assert (await page.text_content("#count")) == "2"
            assert any(u.endswith("/buttons.html") for u in cdp_targets(cdp_url).values())
            await other.close()

    # the server owns the browser: it is gone once the server exits
    for _ in range(50):
        if not cdp_alive(cdp_url):
            break
        await asyncio.sleep(0.1)
    assert not cdp_alive(cdp_url), "the listened-on Chromium outlived the server"


async def test_plain_launch_reports_no_shareable_endpoint(base_url):
    async with mcp_client() as cs:
        info = await call(cs, "browser_info")
        assert info["mode"] == "launched" and info["cdp_url"] is None and info["owns_browser"] is True
        assert "--cdp-listen" in info["hint"] and info["tabs"] == []
