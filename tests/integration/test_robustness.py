"""Robustness: attaching over CDP to a Chromium we launched ourselves, wrap mode driven by an
independent CDP client, isolation between pages of one browser, and structured receipts for a
crashed or closed tab (no hang, no exception)."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request

import pytest
from playwright.async_api import async_playwright

from action_receipt.session import ReceiptSession
from action_receipt.settle import SettleConfig
from tests.conftest import validate_all


def _alive(url: str) -> bool:
    try:
        return bool(json.loads(urllib.request.urlopen(f"{url}/json/version", timeout=2).read()))
    except Exception:
        return False


def _targets(url: str) -> dict[str, str]:
    """Page targets as the browser itself reports them (id -> url), independent of any client."""
    data = json.loads(urllib.request.urlopen(f"{url}/json/list", timeout=2).read())
    return {t["id"]: t["url"] for t in data if t.get("type") == "page"}


async def test_cdp_attach_shares_default_context_and_leaves_browser_running(own_chromium, base_url):
    s = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=8000))
    await s.start(cdp=own_chromium)
    try:
        assert not s.owns_browser and not s.owns_context
        n_before = len(s.pages())
        await s.new_page(f"{base_url}/buttons.html")
        _, r = await s.click("#counter-btn")
        assert r.verdict.verdict == "changed", r.to_dict()
        assert any(
            m["path"].endswith("span#count") and m["after"].endswith("|t=1") for m in r.delta.nodes_modified
        )
        await s.new_page(f"{base_url}/short.html")
        _, r2 = await s.scroll(direction="down")
        assert r2.verdict.verdict == "no_op", r2.to_dict()
        assert validate_all(s) == 2
        for p in list(s.pages())[n_before:]:
            await p.close()
        assert len(s.pages()) == n_before
    finally:
        await s.close()
    assert _alive(own_chromium), "closing the session must not close a browser it did not launch"


async def test_cdp_wrap_mode_around_an_independent_cdp_client(own_chromium, base_url):
    """receipt_begin / receipt_end around an action performed by a *different* CDP client on
    the same browser (the playwright-mcp / browser-use situation)."""
    s = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=8000))
    await s.start(cdp=own_chromium)
    other_pw = await async_playwright().start()
    other = await other_pw.chromium.connect_over_cdp(own_chromium)
    try:
        page = await s.new_page(f"{base_url}/buttons.html")
        rid = await s.begin("external-click", selector="#counter-btn")
        # the other client finds the same tab and clicks
        other_page = next(p for p in other.contexts[0].pages if p.url == page.url)
        await other_page.click("#counter-btn")
        r = await s.end()
        assert r.id == rid and r.args["mode"] == "wrap"
        assert r.verdict.verdict == "changed", r.to_dict()
        assert any(
            m["path"].endswith("span#count") and m["after"].endswith("|t=1") for m in r.delta.nodes_modified
        )

        # and a wrapped action that was never performed
        await s.begin("external-nothing")
        r2 = await s.end()
        assert r2.verdict.verdict == "no_op", r2.to_dict()
        assert validate_all(s) == 2
    finally:
        await other.close()
        await other_pw.stop()
        await s.close()
    assert _alive(own_chromium)


async def test_cdp_new_context_never_touches_existing_tabs(own_chromium, base_url):
    """The 'user' has tabs open; --cdp-new-context must leave every one of them exactly as it
    was (same target ids, same URLs) and remove only its own tab on close."""
    pw = await async_playwright().start()
    user = await pw.chromium.connect_over_cdp(own_chromium)
    user_page = await user.contexts[0].new_page()
    await user_page.goto(f"{base_url}/form.html")
    before = _targets(own_chromium)
    assert any(u.endswith("/form.html") for u in before.values())

    s = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=8000))
    await s.start(cdp=own_chromium, new_context=True)
    try:
        assert s.owns_context and not s.owns_browser
        assert s.pages() == []  # the incognito context starts empty: no user tab is visible to it
        await s.new_page(f"{base_url}/short.html")
        _, r = await s.scroll()
        assert r.verdict.verdict == "no_op"
        during = _targets(own_chromium)
        assert {k: during[k] for k in before} == before  # every pre-existing tab untouched
        assert len(during) == len(before) + 1
    finally:
        await s.close()
    assert _targets(own_chromium) == before  # our tab (and context) are gone, theirs remain
    assert await user_page.evaluate("document.title") == "Form"
    await user.close()
    await pw.stop()


async def test_two_pages_in_one_browser_do_not_cross_contaminate(session, base_url):
    """Page B mutates continuously; a receipt on page A must not see B's mutations, network or
    tabs, and must settle in its own time."""
    a = await session.new_page(f"{base_url}/buttons.html")
    b = await session.new_page(f"{base_url}/ticker.html")
    await asyncio.sleep(0.3)  # B's ticker establishes itself as background
    _, ra = await session.click("#counter-btn", page=a)
    assert ra.verdict.verdict == "changed", ra.to_dict()
    assert ra.settlement.settled and not ra.settlement.timed_out
    assert ra.settlement.elapsed_ms < 1000, ra.settlement.to_dict()
    assert ra.delta.new_tabs == [] and ra.before.url.endswith("/buttons.html")
    assert all(m["path"].endswith("span#count") for m in ra.delta.nodes_modified)

    _, rb = await session.click("#counter", page=b)
    assert rb.verdict.verdict == "changed" and rb.delta.nodes_background == ["span#tick"], rb.to_dict()
    assert rb.settlement.signals["dom"]["background_mutations"] >= 1

    _, ra2 = await session.scroll(page=a)  # A sees none of B's background activity
    assert ra2.delta.background_roots == [] and ra2.settlement.signals["dom"]["background_mutations"] == 0
    assert ra2.verdict.verdict == "no_op", ra2.to_dict()
    assert ra2.settlement.elapsed_ms < 1000


async def test_closed_tab_yields_structured_unknown_receipt_fast(session, base_url):
    page = await session.new_page(f"{base_url}/buttons.html")
    await page.close()
    t = time.perf_counter()
    _, r = await session.click("#counter-btn", page=page)
    assert (time.perf_counter() - t) < 3.0
    assert r.verdict.verdict == "unknown", r.to_dict()
    assert r.verdict.evidence[0] == "page_closed"
    assert r.settlement.aborted == "page_closed" and r.settlement.settled_by == "page_closed"
    assert not r.settlement.settled and not r.settlement.timed_out
    assert r.dispatch["ok"] is False and r.dispatch["error"]
    assert not r.before.ok and not r.after.ok
    assert "open a new tab" in r.verdict.hint
    # the session is still usable
    await session.new_page(f"{base_url}/buttons.html")
    _, r2 = await session.click("#counter-btn")
    assert r2.verdict.verdict == "changed"


async def test_crashed_tab_yields_structured_unknown_receipt_fast(session, base_url):
    page = await session.new_page(f"{base_url}/buttons.html")
    with pytest.raises(Exception):
        await page.goto("chrome://crash", timeout=3000)
    await asyncio.sleep(0.2)
    t = time.perf_counter()
    _, r = await session.click("#counter-btn", page=page)
    assert (time.perf_counter() - t) < 4.0
    assert r.verdict.verdict == "unknown", r.to_dict()
    assert r.verdict.evidence[0] == "page_crashed"
    assert r.settlement.aborted == "page_crashed"
    assert r.delta.page_crashed
    assert "crashed" in r.verdict.hint
    await session.new_page(f"{base_url}/buttons.html")
    _, r2 = await session.click("#counter-btn")
    assert r2.verdict.verdict == "changed"
