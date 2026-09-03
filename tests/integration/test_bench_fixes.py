"""Regression tests for the findings of the benchmark suite (bench/FINDINGS.md F7, F8, F4/F5 and
the accuracy misses). Each test pins one finding: the page shape that produced the wrong verdict,
the verdict that is now produced, and the evidence that explains it."""

from __future__ import annotations

import pytest


def _ev(r, prefix: str) -> list[str]:
    return [e for e in r.verdict.evidence if e.startswith(prefix)]


def _landed(r) -> bool:
    return any(k.endswith("span#result") and "FINAL" in v for k, v in r.after.nodes.items())


# ----------------------------------------------------------------------------- F8: transient spinner
@pytest.mark.parametrize("delay", [250, 1000, 2000])
async def test_f8_spinner_started_by_the_action_is_waited_on(session, base_url, delay):
    """click -> fetch(delay) -> perpetual CSS spinner for delay/2 -> result. The spinner started
    after dispatch, so it is the action's own work: settlement stays busy until it is removed and
    the after-capture holds the rendered result, not the loading state (bench F8: 100/160 stale)."""
    await session.new_page(f"{base_url}/twostage.html?delay={delay}")
    _, r = await session.click("#go")
    assert r.settlement.settled and not r.settlement.timed_out, r.settlement.to_dict()
    assert _landed(r), r.after.nodes.get(next(k for k in r.after.nodes if k.endswith("span#result")))
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(
        m["path"].endswith("span#result") and f"FINAL:{delay}" in m["after"] for m in r.delta.nodes_modified
    ), r.delta.nodes_modified
    assert not any(p.endswith("span#spin") for p in r.delta.nodes_added)  # the spinner is gone
    an = r.settlement.signals["animations"]
    assert an["infinite"] >= 1 and an["background"] == 0, an  # it was perpetual, and not pre-existing
    assert an["busy_until_ms"] is not None and an["busy_until_ms"] >= delay, (
        an
    )  # it kept settlement busy after the fetch
    assert r.settlement.settled_by in ("dom", "animations"), r.settlement.settled_by
    assert r.settlement.elapsed_ms >= delay * 1.5, (
        r.settlement.to_dict()
    )  # fetch + spinner, then the quiet window


async def test_f8_perpetual_spinner_already_running_is_background_and_settles(session, base_url):
    """The other side of F8: a spinner that was running before dispatch is background, even for a
    click right after load, and never keeps the page busy."""
    await session.new_page(f"{base_url}/bg.html?bg=css")
    _, r = await session.click("#noop")
    assert r.settlement.settled and r.settlement.elapsed_ms < 1500, r.settlement.to_dict()
    an = r.settlement.signals["animations"]
    assert an["background"] == 1 and an["infinite"] == 1 and an["busy_until_ms"] is None, an
    assert r.verdict.verdict == "no_op", r.to_dict()

    _, r2 = await session.click("#counter")
    assert r2.verdict.verdict == "changed" and r2.settlement.settled and r2.settlement.elapsed_ms < 1500, (
        r2.to_dict()
    )
    assert r2.settlement.signals["animations"]["background"] == 1


# ----------------------------------------------------------------------------- F7: below-the-fold canvas
async def test_f7_canvas_paint_below_the_fold_is_changed(session, base_url):
    """The dispatcher's scroll-into-view now happens *before* the before-capture, so the scroll
    is not in the delta and the canvas paint (screenshot only) is the whole delta (bench F7)."""
    await session.new_page(f"{base_url}/canvas.html")
    _, r = await session.click("#canvas")
    pf = r.dispatch["preflight"]
    assert pf["prescroll"]["scrolled"] and pf["prescroll"]["dy"] > 0, pf
    assert pf["inViewport"] is True  # probed after the pre-scroll
    assert r.delta.scroll_dy == 0 and r.delta.scroll_dx == 0  # the scroll is not part of the delta
    assert r.before.scroll["y"] > 0  # the before-capture was taken post-scroll
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.screenshot_changed and _ev(r, "screenshot_changed")
    assert not r.delta.dom_changed
    assert _ev(r, "scrolled_into_view_before_capture") and not _ev(r, "scrolled_into_view_only")

    # and a below-the-fold no-op is still a no-op (the F1 case) - the scroll does not become an effect
    _, r2 = await session.click("#silent")
    assert r2.verdict.verdict == "no_op", r2.to_dict()
    assert r2.delta.scroll_dy == 0 and _ev(r2, "focus_moved_only")


# ----------------------------------------------------------------------------- k03 / k07 / css: activity right after load
@pytest.mark.parametrize("bg,root", [("fast", "span#fast"), ("raf", "div#raf"), ("css", None)])
async def test_permanent_background_activity_is_no_op_for_an_unrelated_click_right_after_load(
    session, base_url, bg, root
):
    """A 50 ms setTimeout ticker, a rAF inline-style loop and a CSS-only perpetual animation. The
    click comes immediately after load - before the ticker has shown 200 ms of cadence - which is
    what made the bench read `changed` with never_settled: the before-capture now waits (bounded)
    for the page to be quiet or for its activity to show itself."""
    await session.new_page(f"{base_url}/bg.html?bg={bg}")
    _, r = await session.click("#noop")
    ps = r.dispatch["pre_settle"]
    assert r.settlement.settled and not r.settlement.timed_out, r.settlement.to_dict()
    assert r.settlement.elapsed_ms < 1500
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert not _ev(r, "never_settled")
    if root:
        assert root in r.delta.background_roots, r.delta.background_roots
        assert ps["background_roots"] >= 1 and ps["quiet"] and ps["elapsed_ms"] <= ps["max_ms"] + 100, ps
        assert r.settlement.signals["dom"]["background_mutations"] >= 1
        assert "already mutating before the action" in r.verdict.hint
    else:
        assert r.settlement.signals["animations"]["background"] == 1
    if bg == "fast":
        # the ticker re-arms itself with setTimeout: those timers are background, never awaited
        assert (
            r.settlement.signals["timers"]["busy_until_ms"] is None
            and r.settlement.signals["timers"]["background"] >= 1
        )
    if bg == "raf":
        assert r.delta.screenshot_masked_regions >= 1  # the moving box's pixels were set aside

    # a click that does something on the same page is still changed, naming only its own node
    _, r2 = await session.click("#counter")
    assert r2.verdict.verdict == "changed" and r2.settlement.settled, r2.to_dict()
    assert [m["path"] for m in r2.delta.nodes_modified] == ["span#count"], r2.delta.nodes_modified


async def test_raf_loop_click_on_non_focusable_text_is_no_op_via_screenshot_mask(session, base_url):
    """Without the focus move to hide behind, the moving box's pixels alone would have made the
    screenshot measure say `changed`; its box is masked because it is a background root."""
    await session.new_page(f"{base_url}/bg.html?bg=raf")
    _, r = await session.click("#para")
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert not r.delta.focus_changed
    assert r.delta.screenshot_masked_regions >= 1 and not r.delta.screenshot_changed, r.delta.to_dict()
    assert "div#raf" in r.delta.background_roots


# ----------------------------------------------------------------------------- timers the action scheduled
@pytest.mark.parametrize("ms", [150, 400, 1500])
async def test_timer_scheduled_by_the_action_is_awaited_and_its_effect_caught(session, base_url, ms):
    """A setTimeout(ms) DOM change with nothing else keeping the page busy used to be missed by
    any quiet window shorter than ms (documented bound). The timer the action scheduled is now a
    settlement signal, so the effect is caught at the default quiet window."""
    await session.new_page(f"{base_url}/late.html?ms={ms}")
    _, r = await session.click("#go")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert any(
        m["path"].endswith("span#late") and "appeared" in m["after"] for m in r.delta.nodes_modified
    ), r.delta.nodes_modified
    tm = r.settlement.signals["timers"]
    assert tm["peak_pending"] >= 1 and tm["max_delay_ms"] == ms and tm["long"] == [], tm
    assert tm["busy_until_ms"] is not None and tm["busy_until_ms"] >= ms - 60, tm  # busy until it fired
    assert r.settlement.settled_by in ("timers", "dom"), r.settlement.settled_by
    assert ms <= r.settlement.elapsed_ms < ms + 700, r.settlement.to_dict()
    assert r.settlement.signals["quiet_ms"] == 100 and r.timing["observed_window"] >= ms


async def test_navigation_scheduled_by_a_250ms_timer_is_navigated(session, base_url):
    """bench accuracy s07 / FINDINGS F4: location.assign after a 250 ms timer."""
    await session.new_page(f"{base_url}/timers.html")
    _, r = await session.click("#assign-late")
    assert r.verdict.verdict == "navigated", r.to_dict()
    assert r.delta.url_changed and r.after.url.endswith("/short.html")
    assert r.settlement.signals["timers"]["busy_until_ms"] is not None
    assert r.settlement.elapsed_ms >= 250


async def test_timer_longer_than_timer_wait_is_reported_not_awaited(session, base_url):
    await session.new_page(f"{base_url}/timers.html")
    _, r = await session.click("#long")
    assert r.settlement.settled and r.settlement.elapsed_ms < 1500, r.settlement.to_dict()
    tm = r.settlement.signals["timers"]
    assert tm["long"] == [6000] and tm["peak_pending"] == 0 and tm["busy_until_ms"] is None, tm
    assert r.verdict.verdict == "no_op", r.to_dict()
    assert any("long_timers_not_awaited" in e and "6000" in e for e in r.verdict.evidence), r.verdict.evidence

    session.settle_cfg.timer_wait_ms = 7000  # with a longer allowance the same timer is awaited
    await session.new_page(f"{base_url}/timers.html")
    _, r2 = await session.click("#long")
    assert r2.verdict.verdict == "changed" and r2.settlement.elapsed_ms >= 6000, r2.to_dict()
    assert any(m["path"].endswith("span#out") and "long fired" in m["after"] for m in r2.delta.nodes_modified)


async def test_cleared_timer_releases_settlement_and_chained_timers_are_followed(session, base_url):
    await session.new_page(f"{base_url}/timers.html")
    _, r = await session.click("#cleared")
    assert r.settlement.settled and r.settlement.elapsed_ms < 400, (
        r.settlement.to_dict()
    )  # cleared at 50 ms, not awaited to 400
    assert r.verdict.verdict == "no_op", r.to_dict()

    _, r2 = await session.click("#chain")
    assert r2.verdict.verdict == "changed", r2.to_dict()
    assert any(m["path"].endswith("span#out") and "step 2" in m["after"] for m in r2.delta.nodes_modified), (
        r2.delta.nodes_modified
    )
    assert r2.settlement.elapsed_ms >= 400  # both 200 ms hops were awaited


async def test_timers_signal_can_be_disabled_to_expose_the_quiet_window_bound(session, base_url):
    """timer_wait_ms=0 restores the documented bound: a 400 ms timer is missed at quiet 100 and the
    receipt says so (settled_by quiet_window) rather than pretending."""
    session.settle_cfg.timer_wait_ms = 0
    await session.new_page(f"{base_url}/late.html?ms=400")
    _, r = await session.click("#go")
    assert r.verdict.verdict == "no_op" and r.settlement.settled_by == "quiet_window", r.to_dict()
    assert r.settlement.signals["timers"]["long"] == [400] and _ev(r, "long_timers_not_awaited")
    assert r.settlement.elapsed_ms < 400


# ----------------------------------------------------------------------------- cost
async def test_pre_settle_cost_is_bounded_by_the_quiet_window_and_zero_once_the_page_has_been_seen_quiet(
    session, base_url
):
    """Right after load the page has not been watched for a full quiet window yet, so the first
    action waits out the remainder (<= quiet_ms). Every later action on a quiet page pays one
    evaluate. A target already in view is never scrolled."""
    await session.new_page(f"{base_url}/buttons.html")
    _, r = await session.click("#noop-btn")
    ps = r.dispatch["pre_settle"]
    assert ps["quiet"] and ps["elapsed_ms"] <= 100 + 60, ps
    assert "prescroll" not in r.dispatch["preflight"]  # target was in view: nothing was scrolled
    assert r.verdict.verdict == "no_op"

    _, r2 = await session.click("#noop-btn")
    ps2 = r2.dispatch["pre_settle"]
    assert ps2["quiet"] and ps2["polls"] == 1 and ps2["elapsed_ms"] < 40, ps2  # one evaluate, no wait
    assert r2.timing["pre_settle"] < 40


# ----------------------------------------------------------------------------- F9: screenshot noise floor
async def test_f9_press_enter_on_focused_handlerless_button_is_no_op_not_changed(session, base_url):
    """click #save (rejected: no_op, focus now on the button), then press Enter on it. The
    button's :active repaint is a 0.002-pixel / dhash-0 screenshot delta - the noise floor - and
    used to flip the verdict to `changed` with no other evidence (bench F9, the one verdict a DONE
    gate keys on). A screenshot-only change now needs a perceptual signal to count."""
    await session.new_page(f"{base_url}/press.html")
    _, r0 = await session.click("#save")
    assert r0.verdict.verdict == "no_op", r0.to_dict()
    for sel in ("#save", None):  # with and without a selector: focus is already on the button either way
        _, r = await session.press("Enter", selector=sel)
        assert r.verdict.verdict == "no_op", r.to_dict()
        assert not r.delta.focus_changed and not r.delta.dom_changed and not r.delta.text_changed
        if r.delta.screenshot_changed:  # the repaint may or may not register at all on a given run
            assert (r.delta.screenshot_changed_fraction or 0) < 0.005 and r.delta.screenshot_hamming == 0, (
                r.delta.to_dict()
            )
            assert _ev(r, "screenshot_changed_weak") and not _ev(r, "screenshot_changed:"), r.verdict.evidence
        assert "'Enter'" in r.verdict.hint


async def test_f9_real_screenshot_only_repaint_on_press_is_still_changed(session, base_url):
    """The other side of the threshold: Enter on #painter fills a 300x120 canvas (no DOM change)."""
    await session.new_page(f"{base_url}/press.html")
    await session.click("#painter")  # focus it (no handler for click)
    _, r = await session.press("Enter", selector="#painter")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert not r.delta.dom_changed and not r.delta.focus_changed
    assert (
        r.delta.screenshot_changed
        and r.delta.screenshot_changed_fraction >= 0.005
        and r.delta.screenshot_hamming >= 1
    ), r.delta.to_dict()
    assert _ev(r, "screenshot_changed:")
