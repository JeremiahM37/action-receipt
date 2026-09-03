"""End-to-end receipt tests against local fixture pages. Every test asserts the verdict AND
the evidence that produced it, so a wrong verdict for the right reason still fails."""

from __future__ import annotations

import asyncio

from action_receipt.capture import capture_state


# ----------------------------------------------------------------------------- 1. scroll
async def test_scroll_on_non_scrollable_page_is_no_op(session, base_url):
    """The browser-use shape: default 'scroll one page' on a page that cannot move."""
    await session.new_page(f"{base_url}/short.html")
    result, r = await session.scroll(direction="down", pages=1.0)
    assert result["wheel"]["dy"] == 600
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert r.delta.scroll_dy == 0
    assert "no_observable_change" in r.verdict.evidence
    assert "not scrollable" in r.verdict.hint
    assert r.settlement.settled and not r.settlement.timed_out


async def test_scroll_on_scrollable_page_is_changed_with_delta(session, base_url):
    await session.new_page(f"{base_url}/long.html")
    result, r = await session.scroll(direction="down", pages=1.0)
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.scroll_dy > 0 and result["wheel"]["dy"] > 0
    assert any(e.startswith("window_scrolled") for e in r.verdict.evidence)
    assert r.before.scroll["y"] == 0 and r.after.scroll["y"] == r.delta.scroll_dy


async def test_scroll_at_bottom_is_no_op_with_at_bottom_hint(session, base_url):
    page = await session.new_page(f"{base_url}/long.html")
    await page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
    _, r = await session.scroll(direction="down", pages=1.0)
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert "already at the bottom" in r.verdict.hint


async def test_scroll_window_when_only_inner_container_scrolls(session, base_url):
    """Page has overflow:hidden; only #panel scrolls. Wheel at viewport centre (over the
    heading) must be a no_op whose hint names the inner container; wheel over the panel scrolls it."""
    await session.new_page(f"{base_url}/inner-scroll.html")
    _, r = await session.scroll(direction="down", pages=1.0)
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert "#panel" in r.verdict.hint, r.verdict.hint
    assert "scroll(selector=" in r.verdict.hint

    _, r2 = await session.scroll(direction="down", pages=0.25, selector="#panel")
    assert r2.verdict.verdict == "changed", r2.to_dict()
    assert r2.delta.container_scroll and r2.delta.container_scroll["delta"] > 0
    assert any(e.startswith("container_scrolled") for e in r2.verdict.evidence)
    assert r2.delta.scroll_dy == 0  # the window itself did not move


# ----------------------------------------------------------------------------- 2. navigation
async def test_click_link_navigates(session, base_url):
    await session.new_page(f"{base_url}/nav.html")
    _, r = await session.click("#go")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert r.delta.url_changed and r.after.url.endswith("/short.html")
    assert r.after.title == "Short page"
    assert r.settlement.signals["navigation"]["count"] >= 1
    assert any(e.startswith("url_changed") for e in r.verdict.evidence)


async def test_fragment_link_is_changed_not_navigated(session, base_url):
    await session.new_page(f"{base_url}/nav.html")
    _, r = await session.click("#frag")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.url_only_fragment and not r.delta.url_changed
    assert r.delta.scroll_dy > 0


# ----------------------------------------------------------------------------- 3. blocked
async def test_click_disabled_button_is_blocked(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#disabled-btn")
    assert (
        r.dispatch["elapsed_ms"] < 1500
    )  # pre-flight saw `disabled`, so the actionability wait was shortened
    assert r.verdict.verdict == "blocked", r.to_dict()
    assert any(e.startswith("target_disabled") for e in r.verdict.evidence)
    assert r.dispatch["ok"] is False  # playwright refused (waited for enabled)
    assert r.dispatch["preflight"]["disabled"] is True
    assert "disabled" in r.verdict.hint
    assert not r.delta.dom_changed


async def test_click_covered_button_is_blocked_with_cover_named(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#covered-btn")
    assert r.verdict.verdict == "blocked", r.to_dict()
    cov = r.dispatch["preflight"]["coveredBy"]
    assert cov and cov["id"] == "shield"
    assert any("target_covered_by" in e and "#shield" in e for e in r.verdict.evidence)
    assert "another element receives the pointer" in r.verdict.hint


# ----------------------------------------------------------------------------- 4. type
async def test_type_into_input_is_changed_with_value_delta(session, base_url):
    await session.new_page(f"{base_url}/form.html")
    _, r = await session.type("#name", "Ada Lovelace")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.value_before == "" and r.delta.value_after == "Ada Lovelace"
    assert any(e.startswith("value_changed") for e in r.verdict.evidence)
    assert r.delta.focus_after == "input#name"


async def test_type_into_readonly_is_blocked(session, base_url):
    await session.new_page(f"{base_url}/form.html")
    _, r = await session.type("#ro", "nope")
    assert r.verdict.verdict == "blocked", r.to_dict()
    assert any(e.startswith("target_not_editable") for e in r.verdict.evidence)
    assert r.delta.value_after == "fixed"


async def test_password_value_is_masked_in_receipt(session, base_url):
    await session.new_page(f"{base_url}/form.html")
    _, r = await session.type("#pw", "s3cret")
    assert r.verdict.verdict == "changed"
    assert r.delta.value_after == "******"
    assert "s3cret" not in str(r.to_dict())


async def test_type_into_div_that_is_not_an_input(session, base_url):
    await session.new_page(f"{base_url}/form.html")
    _, r = await session.type("#fake-input", "hello")
    assert r.verdict.verdict in ("blocked", "no_op"), r.to_dict()
    assert r.delta.value_changed is False


# ----------------------------------------------------------------------------- 5. settlement
async def test_slow_fetch_settlement_waits_for_network(session, base_url):
    """Click triggers a 1.5 s fetch whose response mutates the DOM. Settlement must wait for it,
    report the measured time, and the delta must contain the late mutation. A blind 0.5 s sleep
    (the industry constant) is run for comparison on a second page and must miss it."""
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#slow-btn")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.settlement.settled and not r.settlement.timed_out
    assert r.settlement.elapsed_ms >= 1400, r.settlement.to_dict()
    assert r.settlement.signals["network"]["peak_pending"] >= 1
    assert r.settlement.settled_by in ("network", "dom"), r.settlement.settled_by
    assert any(
        "slow-result" in m["path"] and "slow response after 1500ms" in m["after"]
        for m in r.delta.nodes_modified
    ), r.delta.nodes_modified

    # --- control arm: blind 0.5 s sleep would have captured the pre-response state
    page2 = await session.new_page(f"{base_url}/buttons.html")
    before = await capture_state(page2, with_screenshot=False)
    await page2.click("#slow-btn")
    await asyncio.sleep(0.5)
    after_blind = await capture_state(page2, with_screenshot=False)
    assert before.dom_hash == after_blind.dom_hash, "blind 0.5s sleep unexpectedly saw the change"
    key = next(k for k in before.nodes if k.endswith("span#slow-result"))
    assert after_blind.nodes[key] == before.nodes[key] and "pending" in before.nodes[key]


async def test_delayed_dom_change_shows_the_quiet_window_tradeoff(session, base_url):
    """A setTimeout(350 ms) DOM change with no network and no animation is invisible to ANY finite
    wait shorter than 350 ms - *if* the timer itself is not observed. With the timers signal off
    (timer_wait_ms=0) and the default 100 ms quiet window it is missed, and the receipt says so
    (settled_by=quiet_window, quiet_ms=100, long_timers_not_awaited); with a 500 ms window it is
    caught. With the timers signal on (the default) the pending timer keeps settlement busy and
    the change is caught at quiet 100. The receipt turns the magic constant into a stated measurement."""
    session.settle_cfg.timer_wait_ms = 0
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#delayed-btn")
    assert r.settlement.settled_by == "quiet_window", r.settlement.to_dict()
    assert r.settlement.signals["quiet_ms"] == 100
    assert r.settlement.elapsed_ms < 350
    assert not any("appeared" in m["after"] for m in r.delta.nodes_modified)
    assert r.verdict.verdict == "no_op", (
        r.to_dict()
    )  # focus moved, nothing else - reported as focus_moved_only
    assert any(e.startswith("focus_moved_only") for e in r.verdict.evidence)
    assert r.settlement.signals["timers"]["long"] == [350]  # the receipt names what it did not wait for
    assert any(e.startswith("long_timers_not_awaited") for e in r.verdict.evidence)

    session.settle_cfg.quiet_ms = 500
    await session.new_page(f"{base_url}/buttons.html")
    _, r2 = await session.click("#delayed-btn")
    assert r2.verdict.verdict == "changed", r2.to_dict()
    assert any("appeared" in m["after"] for m in r2.delta.nodes_modified), r2.delta.nodes_modified
    assert r2.settlement.settled_by == "dom"
    assert 350 <= r2.settlement.elapsed_ms < 1500, r2.settlement.to_dict()

    session.settle_cfg.quiet_ms = 100
    session.settle_cfg.timer_wait_ms = 5000  # the default: the timer is a signal
    await session.new_page(f"{base_url}/buttons.html")
    _, r3 = await session.click("#delayed-btn")
    assert r3.verdict.verdict == "changed", r3.to_dict()
    assert any("appeared" in m["after"] for m in r3.delta.nodes_modified), r3.delta.nodes_modified
    assert (
        r3.settlement.settled_by in ("timers", "dom")
        and r3.settlement.signals["timers"]["max_delay_ms"] == 350
    )
    assert 350 <= r3.settlement.elapsed_ms < 1000, r3.settlement.to_dict()


async def test_noop_button_settles_fast(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#noop-btn")
    assert r.verdict.verdict == "no_op", r.to_dict()
    # A button with no handler still takes focus; that is reported but not counted as an effect.
    assert r.delta.focus_changed and not r.delta.dom_changed
    assert any(e.startswith("focus_moved_only") for e in r.verdict.evidence)
    assert r.settlement.elapsed_ms < 1000
    assert r.settlement.settled_by in ("quiet_window", "dom", "scroll", "layout"), r.settlement.settled_by


# ----------------------------------------------------------------------------- 6. modal / dialog
async def test_click_opens_modal_is_changed_with_modal_evidence(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#modal-btn")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.modals_opened, r.delta.to_dict()
    assert any(e.startswith("modal_opened") for e in r.verdict.evidence)
    assert "modal opened" in r.verdict.hint
    assert r.delta.screenshot_changed  # the overlay darkens the page


async def test_js_alert_is_recorded_and_auto_accepted(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#alert-btn")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.dialogs and r.delta.dialogs[0]["type"] == "alert"
    assert "hello from alert" in r.delta.dialogs[0]["message"]
    assert any(e.startswith("js_dialog") for e in r.verdict.evidence)


# ----------------------------------------------------------------------------- 7. new tab
async def test_click_opens_new_tab_is_navigated_with_tab_evidence(session, base_url):
    await session.new_page(f"{base_url}/nav.html")
    _, r = await session.click("#newtab")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert r.delta.new_tabs and r.delta.new_tabs[0].endswith("/long.html")
    assert any(e.startswith("new_tab_opened") for e in r.verdict.evidence)
    assert "new tab opened" in r.verdict.hint
    assert not r.delta.url_changed  # the original tab stayed put


# ----------------------------------------------------------------------------- 8. counter click + wrap mode
async def test_click_counter_changed_lists_the_modified_node(session, base_url):
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#counter-btn")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(
        m["path"].endswith("span#count") and m["after"].endswith("|t=1") for m in r.delta.nodes_modified
    ), r.delta.nodes_modified


async def test_wrap_mode_produces_receipt_for_external_action(session, base_url):
    """receipt_begin / receipt_end around an action the session did NOT perform."""
    page = await session.new_page(f"{base_url}/buttons.html")
    rid = await session.begin("external-click", selector="#counter-btn")
    await page.click("#counter-btn")  # 'some other tool'
    r = await session.end()
    assert r.id == rid
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.args["mode"] == "wrap"
    assert any(m["path"].endswith("span#count") for m in r.delta.nodes_modified)

    rid2 = await session.begin("external-nothing")
    r2 = await session.end()
    assert r2.id == rid2 and rid2 != rid
    assert r2.verdict.verdict == "no_op", r2.to_dict()


async def test_receipt_is_json_serialisable_and_has_schema_keys(session, base_url):
    import json

    await session.new_page(f"{base_url}/short.html")
    _, r = await session.scroll()
    d = r.to_dict()
    json.dumps(d)
    for k in (
        "id",
        "action",
        "verdict",
        "evidence",
        "hint",
        "dispatch",
        "settlement",
        "delta",
        "before",
        "after",
        "timing_ms",
    ):
        assert k in d, k
    assert d["verdict"] in ("changed", "no_op", "navigated", "blocked", "unknown")
    assert set(d["settlement"]["signals"]) >= {
        "network",
        "dom",
        "scroll",
        "layout",
        "animations",
        "document",
        "navigation",
    }
