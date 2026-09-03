"""Wrap mode end to end: the receipt server is attached over CDP, a *plain Playwright script*
(standing in for playwright-mcp, raw CDP or a human) performs the action on the same browser
between ``receipt_begin`` and ``receipt_end``, and the receipt describes what that script did."""

from __future__ import annotations

from playwright.async_api import async_playwright

from .conftest import call, cdp_open_tab, mcp_client


async def test_plain_playwright_action_gets_a_receipt(own_chromium, base_url):
    cdp_open_tab(own_chromium, f"{base_url}/buttons.html")
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(own_chromium)
        ctx = browser.contexts[0]
        page = next(p for p in ctx.pages if p.url.endswith("/buttons.html"))
        await page.wait_for_load_state()

        async with mcp_client("--cdp", own_chromium) as cs:
            # 1. an action that changes the page
            begin = await call(
                cs,
                "receipt_begin",
                label="playwright-click",
                page_url="buttons.html",
                selector="#counter-btn",
            )
            assert begin["observing"].endswith("/buttons.html")
            await page.click("#counter-btn")  # the other tool acts
            rc = (await call(cs, "receipt_end"))["receipt"]
            assert rc["action"]["name"] == "playwright-click" and rc["action"]["args"]["mode"] == "wrap"
            assert rc["dispatch"]["mode"] == "wrap" and rc["dispatch"]["ok"] is None
            assert rc["verdict"] == "changed", rc["evidence"]
            assert any(
                m["path"].endswith("span#count") and m["after"].endswith("|t=1")
                for m in rc["delta"]["nodes_modified"]
            )
            assert "dispatch" not in rc["timing_ms"] and rc["timing_ms"]["observed_window"] > 0

            # 2. the other tool does nothing between begin and end
            await call(cs, "receipt_begin", label="playwright-idle", page_url="buttons.html")
            rc = (await call(cs, "receipt_end"))["receipt"]
            assert rc["verdict"] == "no_op" and "re-observe" in rc["hint"]

            # 3. the other tool navigates
            await call(cs, "receipt_begin", label="playwright-goto", page_url="buttons.html")
            await page.goto(f"{base_url}/short.html")
            rc = (await call(cs, "receipt_end"))["receipt"]
            assert rc["verdict"] == "navigated" and rc["delta"]["url_changed"]
            assert rc["after"]["url"].endswith("/short.html")

            # 4. the other tool types: the value delta is attributed to the wrapped action
            await page.goto(f"{base_url}/form.html")
            await call(cs, "receipt_begin", label="playwright-fill", page_url="form.html", selector="#name")
            await page.fill("#name", "Ada")
            rc = (await call(cs, "receipt_end"))["receipt"]
            assert rc["verdict"] == "changed" and rc["delta"]["value_after"] == "Ada"

            # receipt_end twice is a structured error, not a protocol error
            res = await cs.call_tool("receipt_end", {})
            assert not res.is_error and res.structured_content["error"]["type"] == "RuntimeError"
        await browser.close()
