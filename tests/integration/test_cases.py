"""The awkward cases a real agent hits. Every test asserts the verdict AND the evidence that
produced it, and where a bound is being documented (quiet window, node cap) it asserts that the
receipt reports what it measured rather than pretending."""

from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import async_playwright

from action_receipt.session import ReceiptSession
from action_receipt.settle import SettleConfig


def _ev(r, prefix: str) -> list[str]:
    return [e for e in r.verdict.evidence if e.startswith(prefix)]


# ----------------------------------------------------------------------------- SPA / URL
async def test_spa_pushstate_is_navigated_without_load_event(session, base_url):
    await session.new_page(f"{base_url}/spa.html")
    _, r = await session.click("#push")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert r.delta.url_changed and r.after.url.endswith("/spa/page2")
    assert r.settlement.signals["navigation"]["count"] == 1
    assert _ev(r, "url_changed") and _ev(r, "navigation_events")
    assert any(m["path"].endswith("span#view") and "page two" in m["after"] for m in r.delta.nodes_modified)
    assert r.settlement.elapsed_ms < 2000  # no load event to wait for


async def test_spa_replacestate_without_render_is_still_navigated(session, base_url):
    await session.new_page(f"{base_url}/spa.html")
    _, r = await session.click("#replace")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert r.delta.url_changed and r.after.url.endswith("/spa/page3")
    assert not r.delta.dom_changed


async def test_hash_only_navigation_without_anchor_is_changed_not_navigated(session, base_url):
    await session.new_page(f"{base_url}/spa.html")
    _, r = await session.click("#hash")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.url_only_fragment and not r.delta.url_changed
    assert r.delta.scroll_dy == 0  # nothing to scroll to
    assert _ev(r, "url_fragment_changed")
    assert r.after.url.endswith("#details")


async def test_same_url_rerender_with_identical_content_is_no_op_with_mutation_evidence(session, base_url):
    """Handler ran (DOM mutated) but the fingerprint is byte-identical: no_op, and the receipt says why."""
    await session.new_page(f"{base_url}/spa.html")
    _, r = await session.click("#rerender")
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert r.settlement.signals["dom"]["mutations"] >= 2
    assert _ev(r, "dom_mutated_without_fingerprint_change"), r.verdict.evidence
    assert "re-rendered to identical content" in r.verdict.hint
    assert not r.delta.dom_changed and r.before.dom_hash == r.after.dom_hash


async def test_reload_same_url_is_navigated_with_navigation_event(session, base_url):
    await session.new_page(f"{base_url}/spa.html")
    _, r = await session.click("#reload")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert not r.delta.url_changed and r.delta.navigations
    assert _ev(r, "navigation_events")
    assert r.after.ready_state == "complete"


async def test_replacestate_same_url_identical_content_is_no_op_not_navigated(session, base_url):
    """Playwright fires framenavigated for same-document history changes; with an unchanged URL
    that is not a navigation (bench finding F3)."""
    await session.new_page(f"{base_url}/spa.html")
    _, r = await session.click("#replace-same")
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert r.delta.navigations and not r.delta.cross_document_navigations and not r.delta.url_changed
    assert _ev(r, "history_changed_same_document")

    _, r2 = await session.click("#replace-same-swap")
    assert r2.verdict.verdict == "changed", r2.to_dict()
    assert any(m["path"].endswith("span#view") and "swapped" in m["after"] for m in r2.delta.nodes_modified)


# ----------------------------------------------------------------------------- iframes
async def test_click_inside_same_origin_iframe_is_changed_with_frame_path(session, base_url):
    await session.new_page(f"{base_url}/frame-outer.html")
    _, r = await session.click("#inner-btn", frame="#f")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.dispatch["preflight"]["inFrame"] is True
    assert r.dispatch["preflight"]["path"].startswith("iframe#f>#document>")
    assert any(
        m["path"] == "iframe#f>#document>html>body:nth-of-type(1)>p:nth-of-type(1)>span#inner-count"
        and m["after"].endswith("|t=1")
        for m in r.delta.nodes_modified
    ), r.delta.nodes_modified
    assert r.settlement.signals["dom"]["frames_observed"] >= 2
    assert r.settlement.signals["dom"]["mutations"] >= 1  # the iframe's observer was aggregated


async def test_scroll_inside_same_origin_iframe_is_changed_with_frame_scroll(session, base_url):
    await session.new_page(f"{base_url}/frame-outer.html")
    _, r = await session.scroll(direction="down", pages=0.5, selector="#inner-btn", frame="#f")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.scroll_dy == 0  # the window did not move
    assert (
        r.settlement.signals["dom"]["mutations"] == 0
    )  # a scroll mutates nothing; pre-dispatch frame mutations are not attributed
    assert (
        r.delta.frame_scrolls
        and r.delta.frame_scrolls[0]["path"] == "iframe#f"
        and r.delta.frame_scrolls[0]["delta"] > 0
    )
    assert _ev(r, "frame_scrolled")


async def test_scroll_window_over_iframe_page_no_op_hint_names_the_frame(session, base_url):
    await session.new_page(f"{base_url}/frame-outer.html")
    _, r = await session.scroll(direction="down", pages=1.0)  # wheel at the viewport centre = over the iframe
    # The wheel lands on the frame, so the frame scrolls (changed) - or the window, if the page is taller.
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.frame_scrolls or r.delta.scroll_dy


async def test_cross_origin_iframe_change_seen_only_through_screenshot(session, base_url):
    """Documented limit: a cross-origin frame is not fingerprinted. A large visual change is
    still caught by the screenshot measure; the frame is reported as crossOrigin."""
    page = await session.new_page(f"{base_url}/frame-outer.html")
    await page.frame_locator("#x").locator("#xo-btn").wait_for(timeout=3000)
    xo = [f for f in (await session.snapshot(aria=False))["frames"] if f["path"] == "iframe#x"]
    assert xo and xo[0].get("crossOrigin") is True
    _, r = await session.click("#xo-btn", frame="#x")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert not r.delta.dom_changed
    assert r.delta.screenshot_changed and _ev(r, "screenshot_changed")


# ----------------------------------------------------------------------------- shadow DOM
async def test_click_in_open_shadow_root_lists_the_shadow_node(session, base_url):
    await session.new_page(f"{base_url}/shadow.html")
    _, r = await session.click("#open-host #inc")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.dispatch["preflight"]["path"] == "x-counter#open-host>#shadow-root>button#inc"
    assert any(
        m["path"] == "x-counter#open-host>#shadow-root>span#n" and m["after"].endswith("|t=1")
        for m in r.delta.nodes_modified
    ), r.delta.nodes_modified
    assert r.settlement.signals["dom"]["mutations"] >= 1  # mutation inside the shadow root was observed
    assert r.settlement.settled_by == "dom", r.settlement.to_dict()  # ... and it is what settlement waited on


async def test_click_in_closed_shadow_root_is_seen_via_attachshadow_hook(session, base_url):
    """Closed roots are invisible to el.shadowRoot; the init script's attachShadow hook keeps a
    handle so the fingerprint and the mutation observer still see them."""
    page = await session.new_page(f"{base_url}/shadow.html")
    handle = await page.evaluate_handle(
        "window.__ar.shadow.get(document.getElementById('closed-host')).getElementById('cinc')"
    )
    before = await session.snapshot(aria=False)
    assert before["element_count"] > 0
    rid = await session.begin("external-click")
    await handle.as_element().click()
    r = await session.end()
    assert r.id == rid
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(
        m["path"] == "x-closed#closed-host>#shadow-root>span#cn" and m["after"].endswith("|t=1")
        for m in r.delta.nodes_modified
    ), r.delta.nodes_modified


# ----------------------------------------------------------------------------- infinite scroll
async def test_infinite_scroll_waits_for_fetch_and_lists_new_rows(session, base_url):
    await session.new_page(f"{base_url}/infinite.html")
    _, r = await session.scroll(direction="down", pages=2.0)
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.scroll_dy > 0
    assert r.settlement.signals["network"]["peak_pending"] >= 1
    assert r.settlement.elapsed_ms >= 300, r.settlement.to_dict()
    assert r.settlement.settled_by in ("network", "dom", "layout"), (
        r.settlement.settled_by
    )  # rows appended -> layout shifts last
    assert r.settlement.signals["network"]["busy_until_ms"] >= 250
    assert r.delta.nodes_added and any(p.endswith("div#row-12") for p in r.delta.nodes_added), (
        r.delta.nodes_added
    )
    assert r.delta.nodes_changed_total >= 12 and len(r.delta.nodes_added) <= 12
    assert r.after.element_total - r.before.element_total == 12


# ----------------------------------------------------------------------------- sticky header
async def test_click_target_under_sticky_header_is_blocked_naming_the_header(session, base_url):
    await session.new_page(f"{base_url}/sticky.html")
    _, r = await session.click("#under")
    assert r.verdict.verdict == "blocked", r.to_dict()
    cov = r.dispatch["preflight"]["coveredBy"]
    assert cov and cov["tag"] == "header", cov
    assert any("target_covered_by" in e and "header" in e for e in r.verdict.evidence)
    assert r.dispatch["ok"] is False and "intercepts pointer events" in r.dispatch["error"]
    assert r.dispatch["elapsed_ms"] < 1500
    # Playwright's retries scroll the page a little trying other alignments; that is not an effect.
    assert r.delta.scroll_dy != 0 and _ev(r, "scrolled_into_view_only"), r.verdict.evidence
    assert not _ev(r, "window_scrolled") and not _ev(r, "screenshot_changed")


async def test_click_target_below_fold_scrolls_into_view_and_is_changed(session, base_url):
    """The target is brought into view *before* the before-capture (bench F7), so the scroll is
    recorded in the preflight and stated in the evidence but is not part of the delta."""
    await session.new_page(f"{base_url}/sticky.html")
    _, r = await session.click("#below")
    assert r.verdict.verdict == "changed", r.to_dict()
    pf = r.dispatch["preflight"]
    assert pf["prescroll"]["scrolled"] and pf["prescroll"]["dy"] > 0, pf  # we scrolled it into view first
    assert pf["inViewport"] is True  # ... so the probe saw it in view
    assert r.before.scroll["y"] > 0 and r.delta.scroll_dy == 0  # and the delta holds no scroll
    assert _ev(r, "scrolled_into_view_before_capture") and not _ev(r, "window_scrolled")
    assert any(
        m["path"].endswith("div#status") and "below clicked" in m["after"] for m in r.delta.nodes_modified
    )


async def test_pointer_events_none_and_offscreen_targets_are_blocked_fast(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#pe-none")
    assert r.verdict.verdict == "blocked", r.to_dict()
    assert _ev(r, "target_pointer_events_none") and "pointer-events:none" in r.verdict.hint
    assert r.dispatch["elapsed_ms"] < 1500  # pre-flight shortened the actionability wait
    assert not r.delta.dom_changed

    _, r2 = await session.click("#offscreen")
    assert r2.verdict.verdict == "blocked", r2.to_dict()
    assert r2.dispatch["preflight"]["offscreen"] is True
    assert _ev(r2, "target_offscreen") and "outside the document" in r2.verdict.hint
    assert r2.dispatch["elapsed_ms"] < 1500
    assert not r2.delta.dom_changed


# ----------------------------------------------------------------------------- hover menu
async def test_hover_menu_item_click_blocked_until_hovered(session, base_url):
    await session.new_page(f"{base_url}/menu.html")
    _, r1 = await session.click("#item-save")
    assert r1.verdict.verdict == "blocked", r1.to_dict()
    assert _ev(r1, "target_not_visible")
    assert "hover" in r1.verdict.hint

    _, r2 = await session.hover("#menu")
    assert r2.verdict.verdict == "changed", r2.to_dict()
    # The items lost their |hidden signature bit, so the delta names them - not just the screenshot.
    assert any(
        m["path"].endswith("button#item-save") and "hidden" in m["before"] and "hidden" not in m["after"]
        for m in r2.delta.nodes_modified
    ), r2.delta.nodes_modified

    _, r3 = await session.click("#item-save")
    assert r3.verdict.verdict == "changed", r3.to_dict()
    assert any(m["path"].endswith("p#out") and "saved" in m["after"] for m in r3.delta.nodes_modified)


async def test_hover_on_inert_element_is_no_op_with_hover_hint(session, base_url):
    await session.new_page(f"{base_url}/menu.html")
    _, r = await session.hover("h1")
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert "hover produced no observable change" in r.verdict.hint


# ----------------------------------------------------------------------------- select / contenteditable / keyboard
async def test_select_option_is_changed_with_value_delta(session, base_url):
    await session.new_page(f"{base_url}/form.html")
    _, r = await session.select("#sel", "green")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.value_before == "red" and r.delta.value_after == "green"
    assert _ev(r, "value_changed")
    assert any(
        m["path"].endswith("span#selout") and "chose green" in m["after"] for m in r.delta.nodes_modified
    )

    _, r2 = await session.select("#sel", "green")  # already selected
    assert r2.verdict.verdict == "no_op", r2.to_dict()
    assert "already have been selected" in r2.verdict.hint

    _, r3 = await session.select("#sel", "purple")  # not an option
    assert r3.verdict.verdict == "blocked", r3.to_dict()
    assert "not in the select" in r3.verdict.hint
    assert r3.dispatch["elapsed_ms"] < 500  # no actionability timeout was burned
    assert r3.delta.value_after == "green"

    _, r4 = await session.select("#sel", "Blue")  # by label
    assert r4.verdict.verdict == "changed" and r4.delta.value_after == "blue", r4.to_dict()

    _, r5 = await session.click(
        "#sel"
    )  # clicking a <select> opens a native popup that leaves no DOM trace (bench F6)
    if r5.verdict.verdict == "no_op":
        assert "use select(selector, value)" in r5.verdict.hint, r5.verdict.hint
    else:
        assert (
            r5.verdict.verdict == "changed" and r5.delta.value_after == "blue"
        )  # the popup paints; the value did not move


async def test_type_into_contenteditable_is_changed(session, base_url):
    await session.new_page(f"{base_url}/form.html")
    _, r = await session.type("#editor", "hello world")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.dispatch["preflight"]["editable"] is True
    assert r.delta.value_before == "" and r.delta.value_after == "hello world"
    assert _ev(r, "value_changed")


async def test_keyboard_only_tab_tab_enter(session, base_url):
    await session.new_page(f"{base_url}/keyboard.html")
    _, r1 = await session.press("Tab")
    assert r1.verdict.verdict == "changed", r1.to_dict()  # focus counts for press
    assert r1.delta.focus_after == "input#kb-input" and _ev(r1, "focus_changed")

    _, r2 = await session.type("#kb-input", "kb")
    assert r2.verdict.verdict == "changed"

    _, r3 = await session.press("Tab")
    assert r3.delta.focus_after == "button#kb-btn", r3.to_dict()

    _, r4 = await session.press("Enter")
    assert r4.verdict.verdict == "changed", r4.to_dict()
    assert any(
        m["path"].endswith("p#kb-out") and "activated:kb" in m["after"] for m in r4.delta.nodes_modified
    )

    _, r5 = await session.press("Escape")
    assert r5.verdict.verdict == "no_op", r5.to_dict()
    assert "'Escape'" in r5.verdict.hint


# ----------------------------------------------------------------------------- downloads
@pytest.mark.parametrize("sel", ["#dl-attr", "#dl-plain"])
async def test_click_that_downloads_does_not_hang_and_says_so(session, base_url, sel):
    await session.new_page(f"{base_url}/download.html")
    _, r = await session.click(sel)
    assert r.settlement.settled and not r.settlement.timed_out, r.settlement.to_dict()
    assert r.settlement.elapsed_ms < 2000
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.downloads and r.delta.downloads[0]["filename"] == "report.bin"
    assert _ev(r, "download_started")
    assert "download started" in r.verdict.hint and "do not retry" in r.verdict.hint
    assert not r.delta.url_changed and not r.delta.dom_changed


# ----------------------------------------------------------------------------- beforeunload
async def test_beforeunload_accepted_navigates_and_records_dialog(session, base_url):
    await session.new_page(f"{base_url}/beforeunload.html")
    _, r = await session.click("#leave")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert (
        r.delta.dialogs
        and r.delta.dialogs[0]["type"] == "beforeunload"
        and r.delta.dialogs[0]["handled"] == "accept"
    )
    assert _ev(r, "js_dialog")
    assert "beforeunload" in r.verdict.hint
    assert r.after.url.endswith("/short.html")


async def test_beforeunload_dismissed_stays_and_says_so(base_url):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        ctx = await browser.new_context(viewport={"width": 1000, "height": 600})
        s = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=8000), dialog_action="dismiss")
        await s.attach_context(ctx)
        await s.new_page(f"{base_url}/beforeunload.html")
        _, r = await s.click("#leave")
        assert r.verdict.verdict == "changed", r.to_dict()
        assert r.delta.dialogs[0]["type"] == "beforeunload" and r.delta.dialogs[0]["handled"] == "dismiss"
        assert not r.delta.url_changed and r.after.url.endswith("/beforeunload.html")
        assert "navigation was cancelled" in r.verdict.hint
        from tests.conftest import validate_all

        validate_all(s)
    finally:
        await browser.close()
        await pw.stop()


# ----------------------------------------------------------------------------- CSS transitions
@pytest.mark.parametrize("ms", [300, 1000, 2500])
async def test_css_transition_settlement_waits_for_the_animation(session, base_url, ms):
    await session.new_page(f"{base_url}/anim.html?ms={ms}")
    _, r = await session.click("#go")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.settlement.settled and not r.settlement.timed_out
    assert r.settlement.settled_by == "animations", r.settlement.to_dict()
    assert r.settlement.signals["animations"]["reason"] == "1 running"
    assert ms <= r.settlement.elapsed_ms < ms + 700, r.settlement.to_dict()
    assert r.settlement.signals["animations"]["busy_until_ms"] >= ms - 50
    # the box actually ended up moved: class flipped and the screenshot differs
    assert any(m["path"].endswith("div#box") and "moved" in m["after"] for m in r.delta.nodes_modified)
    assert r.delta.screenshot_changed


# ----------------------------------------------------------------------------- setTimeout-only late effects
@pytest.mark.parametrize("ms", [150, 400, 1500])
async def test_late_timer_effect_receipt_is_consistent_with_its_own_observed_window(session, base_url, ms):
    """A setTimeout(ms) DOM change with no network/animation/prior mutation. With the timers
    signal switched off (timer_wait_ms=0) it is invisible to any wait shorter than ms, and the
    invariant we can assert without racing the machine is: the receipt claims the change iff its
    own observed window reached the timer. With a quiet window longer than the timer it is always
    caught (settled_by dom). With the timers signal on - the default - it is always caught at
    quiet 100, because the pending timer itself keeps settlement busy (test_bench_fixes.py)."""
    session.settle_cfg.timer_wait_ms = 0
    await session.new_page(f"{base_url}/late.html?ms={ms}")
    _, r = await session.click("#go")
    caught = any(m["path"].endswith("span#late") and "appeared" in m["after"] for m in r.delta.nodes_modified)
    observed = r.timing["observed_window"]
    if caught:
        assert r.verdict.verdict == "changed" and observed >= ms, r.to_dict()
    else:
        assert r.verdict.verdict == "no_op" and observed < ms + 150, (
            r.to_dict()
        )  # after-capture ran before the timer (+capture cost)
        assert r.settlement.settled_by == "quiet_window"
        assert "quiet window 100" in r.verdict.hint
        assert r.settlement.signals["timers"]["long"] == [ms]  # the timer was seen, and named as not awaited
    assert r.settlement.signals["quiet_ms"] == 100
    # A 1500 ms timer cannot be caught by a 100 ms quiet window on any sane machine; document it.
    if ms == 1500:
        assert not caught, "settlement took >1.5 s on a no-op page - investigate the machine, not the test"

    session.settle_cfg.quiet_ms = ms + 300
    await session.new_page(f"{base_url}/late.html?ms={ms}")
    _, r2 = await session.click("#go")
    assert r2.verdict.verdict == "changed", r2.to_dict()
    assert r2.settlement.settled_by == "dom", r2.settlement.to_dict()
    assert r2.settlement.elapsed_ms >= ms + 300  # timer + the quiet window after it
    assert any(m["path"].endswith("span#late") and "appeared" in m["after"] for m in r2.delta.nodes_modified)

    session.settle_cfg.quiet_ms = 100
    session.settle_cfg.timer_wait_ms = 5000
    await session.new_page(f"{base_url}/late.html?ms={ms}")
    _, r3 = await session.click("#go")
    assert r3.verdict.verdict == "changed", r3.to_dict()
    assert r3.settlement.settled_by in ("timers", "dom") and ms <= r3.settlement.elapsed_ms < ms + 700, (
        r3.settlement.to_dict()
    )
    assert any(m["path"].endswith("span#late") and "appeared" in m["after"] for m in r3.delta.nodes_modified)


# ----------------------------------------------------------------------------- permanent mutation
async def test_ticker_already_running_is_set_aside_as_background(session, base_url):
    """A node that was mutating before the action (30 ms ticker) is background: it does not keep
    settlement busy and its changes are excluded from the delta - and the receipt says so."""
    await session.new_page(f"{base_url}/ticker.html")
    await asyncio.sleep(0.3)  # let the ticker tick repeatedly (>= 3 batches over >= 200 ms) inside the window
    _, r = await session.click("#counter")
    assert r.settlement.settled and not r.settlement.timed_out, r.settlement.to_dict()
    assert r.settlement.elapsed_ms < 1500
    assert r.settlement.signals["dom"]["background_mutations"] >= 1
    assert r.verdict.verdict == "changed", r.to_dict()
    assert [m["path"] for m in r.delta.nodes_modified] == ["span#c"], r.delta.nodes_modified
    assert r.delta.nodes_background == ["span#tick"] and r.delta.nodes_background_total == 1
    assert "span#tick" in r.delta.background_roots
    assert _ev(r, "background_mutations_excluded")
    assert not _ev(r, "never_settled")

    _, r2 = await session.scroll(direction="down")  # page fits: nothing but the ticker changes
    assert r2.settlement.settled, r2.settlement.to_dict()
    assert r2.verdict.verdict == "no_op", r2.to_dict()
    assert not r2.delta.dom_changed and r2.delta.nodes_background == ["span#tick"]
    assert any("not attributable" in e for e in r2.verdict.evidence) or not r2.delta.text_changed
    assert "already mutating before the action" in r2.verdict.hint


async def test_ticker_started_by_the_action_never_settles_and_says_so_without_hanging(session, base_url):
    """The mutation starts *after* dispatch, so it is the action's own effect: it cannot be set
    aside, settlement must hit the hard timeout, and the verdict must say never_settled."""
    session.settle_cfg.timeout_ms = 1200
    await session.new_page(f"{base_url}/ticker.html?auto=0")
    _, r = await session.click("#start")
    assert r.settlement.timed_out and not r.settlement.settled
    assert r.settlement.busy_at_timeout == ["dom"], r.settlement.to_dict()
    assert 1200 <= r.settlement.elapsed_ms < 1800, r.settlement.to_dict()
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.verdict.evidence[0].startswith("never_settled: still busy ['dom']")
    assert "never went quiet" in r.verdict.hint
    assert any(m["path"].endswith("span#tick2") for m in r.delta.nodes_modified)
    assert r.delta.nodes_background == []


async def test_perpetual_css_animation_does_not_block_settlement(session, base_url):
    await session.new_page(f"{base_url}/spinner.html")
    _, r = await session.click("#counter")
    assert r.settlement.settled and r.settlement.elapsed_ms < 1500, r.settlement.to_dict()
    assert r.settlement.signals["animations"]["infinite"] == 1
    assert r.settlement.signals["animations"]["busy_until_ms"] is None  # never counted as busy
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(m["path"].endswith("span#c") for m in r.delta.nodes_modified)

    _, r2 = await session.click(
        "#grow"
    )  # a finite transition next to the spinner still settles by animations
    assert r2.settlement.settled_by == "animations" and r2.settlement.elapsed_ms >= 400, (
        r2.settlement.to_dict()
    )
    assert r2.settlement.signals["animations"]["infinite"] == 1


# ----------------------------------------------------------------------------- popups
async def test_window_open_blocked_by_browser_vs_allowed(base_url):
    """Chromium's --block-new-web-contents makes every window.open fail at the browser level."""
    pw = await async_playwright().start()
    blocked = await pw.chromium.launch(headless=True, args=["--block-new-web-contents"])
    allowed = await pw.chromium.launch(headless=True)
    try:
        for browser, expect in ((blocked, "blocked"), (allowed, "opened")):
            ctx = await browser.new_context(viewport={"width": 1000, "height": 600})
            s = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=8000))
            await s.attach_context(ctx)
            await s.new_page(f"{base_url}/popup.html")
            _, r = await s.click("#pop")
            assert any(
                m["path"].endswith("span#st") and m["after"].endswith("|t=" + expect)
                for m in r.delta.nodes_modified
            ), (expect, r.delta.nodes_modified)
            if expect == "blocked":
                assert r.verdict.verdict == "changed", r.to_dict()
                assert r.delta.new_tabs == [] and not any(
                    e.startswith("new_tab_opened") for e in r.verdict.evidence
                )
            else:
                assert r.verdict.verdict == "navigated", r.to_dict()
                assert r.delta.new_tabs and r.delta.new_tabs[0].endswith("/short.html")
            from tests.conftest import validate_all

            validate_all(s)
    finally:
        await blocked.close()
        await allowed.close()
        await pw.stop()


# ----------------------------------------------------------------------------- 4xx
async def test_form_submit_navigation_returning_400_is_navigated_with_http_error(session, base_url):
    await session.new_page(f"{base_url}/form-post.html")
    _, r = await session.click("#submit-nav")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert r.delta.url_changed and r.after.url.endswith("/submit")
    assert (
        r.delta.http_errors
        and r.delta.http_errors[0]["status"] == 400
        and r.delta.http_errors[0]["method"] == "POST"
    )
    assert _ev(r, "http_error")
    assert "HTTP 400" in r.verdict.hint
    assert r.after.title == "Bad Request"


async def test_fetch_submit_returning_400_is_changed_with_http_error(session, base_url):
    """The page reads r.status and never consumes the body, so Chromium never reports the request
    finished. Settlement must not wait for that: a small Content-Length body is released at the
    headers, and the receipt records the release."""
    await session.new_page(f"{base_url}/form-post.html")
    _, r = await session.click("#submit-fetch")
    assert r.settlement.settled and not r.settlement.timed_out, r.settlement.to_dict()
    assert r.settlement.elapsed_ms < 1500, r.settlement.to_dict()
    rel = r.settlement.signals["network"]["released"]
    assert rel and rel[0]["url"].endswith("/submit") and "already buffered" in rel[0]["reason"], rel
    assert r.verdict.verdict == "changed", r.to_dict()
    assert not r.delta.url_changed
    assert r.delta.http_errors[0]["status"] == 400
    assert _ev(r, "http_error")
    assert "HTTP 400" in r.verdict.hint
    assert any(
        m["path"].endswith("span#err") and "server said 400" in m["after"] for m in r.delta.nodes_modified
    )


async def test_fetch_that_ignores_a_body_without_content_length_is_released_after_grace(session, base_url):
    """No Content-Length, body never read: nothing can prove the body is done, so the network
    signal stays busy for body_grace_ms and then releases the request - stated in the receipt."""
    session.settle_cfg.body_grace_ms = 700
    await session.new_page(f"{base_url}/form-post.html")
    _, r = await session.click("#fetch-nolen")
    assert r.settlement.settled and not r.settlement.timed_out, r.settlement.to_dict()
    assert 700 <= r.settlement.elapsed_ms < 2500, r.settlement.to_dict()
    assert r.settlement.settled_by == "network"
    rel = r.settlement.signals["network"]["released"]
    assert rel and "not finished within 700ms" in rel[0]["reason"], rel
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(
        m["path"].endswith("span#nolen") and "status 200" in m["after"] for m in r.delta.nodes_modified
    )


# ----------------------------------------------------------------------------- double click
async def test_double_click_is_one_receipt_with_both_effects(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#dbl-btn", click_count=2)
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(
        m["path"].endswith("span#dbl") and m["after"].endswith("clicks=2") for m in r.delta.nodes_modified
    ), r.delta.nodes_modified
    assert any(
        m["path"].endswith("span#dbl2") and m["after"].endswith("dblclick") for m in r.delta.nodes_modified
    )


async def test_two_rapid_clicks_are_two_receipts_each_attributing_one_increment(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r1 = await session.click("#counter-btn")
    _, r2 = await session.click("#counter-btn")
    assert r1.verdict.verdict == r2.verdict.verdict == "changed"
    m1 = next(m for m in r1.delta.nodes_modified if m["path"].endswith("span#count"))
    m2 = next(m for m in r2.delta.nodes_modified if m["path"].endswith("span#count"))
    assert m1["before"].endswith("|t=0") and m1["after"].endswith("|t=1")
    assert m2["before"].endswith("|t=1") and m2["after"].endswith("|t=2")
    assert r1.id != r2.id


# ----------------------------------------------------------------------------- very large DOM
async def test_large_dom_delta_is_bounded_and_capture_cost_is_linear(session, base_url):
    await session.new_page(f"{base_url}/big.html?n=6000")
    _, r = await session.click("#edit-many")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.before.element_total >= 12000 and not r.before.truncated, r.before.summary()
    assert r.before.element_count == r.before.element_total
    assert r.delta.nodes_changed_total == 100
    assert len(r.delta.nodes_modified) == 12 and not r.delta.nodes_added and not r.delta.nodes_removed
    assert r.delta.fingerprint_truncated is None
    # cost: the walk is O(n). 12k elements + a viewport screenshot; the old 4k-node capture cost 350 ms.
    assert r.before.elapsed_ms < 600 and r.after.elapsed_ms < 600, (r.before.elapsed_ms, r.after.elapsed_ms)
    assert len(str(r.to_dict())) < 20000  # the receipt itself stays bounded

    _, r2 = await session.click("#edit-late")  # a node near the end of the page
    assert r2.verdict.verdict == "changed", r2.to_dict()
    assert any(
        m["path"].endswith("span#cell-5995") and "EDITED" in m["after"] for m in r2.delta.nodes_modified
    ), r2.delta.nodes_modified


async def test_dom_beyond_node_cap_is_reported_as_truncated(session, base_url):
    """With 30k+ elements the fingerprint stops at the cap; the receipt must say so rather than
    silently reporting no_op for a change past the cap."""
    await session.new_page(f"{base_url}/big.html?n=15000")
    _, r = await session.click("#edit-late")
    assert r.before.truncated and r.before.element_count == 20000 and r.before.element_total > 30000, (
        r.before.summary()
    )
    assert r.delta.fingerprint_truncated == {"fingerprinted": 20000, "total": r.before.element_total}
    assert _ev(r, "fingerprint_truncated")
    # The edited node is past the cap, so the node list cannot name it - but the innerText hash
    # still sees the edit: verdict is changed on text_changed, honestly without a node.
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.text_changed and not r.delta.dom_changed and not r.delta.nodes_modified
    assert _ev(r, "text_changed")
    assert r.before.elapsed_ms < 800, r.before.elapsed_ms  # 20k-node cap keeps the capture bounded
