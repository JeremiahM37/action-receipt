"""The verdict decision table, fed synthetic deltas: every verdict, every evidence token, every
hint branch - no browser."""

from __future__ import annotations

import pytest

from action_receipt.delta import Delta
from action_receipt.verdict import (
    BLOCK_MARKERS,
    VERDICTS,
    Verdict,
    _collect_evidence,
    _dispatcher_scrolled,
    _scroll_hint,
    _visual_is_perceptual,
    substantive_change,
)

from ._synth import SCROLL, URL, delta, settle, verdict_for

TARGET = {
    "path": "button#go",
    "tag": "button",
    "visible": True,
    "inViewport": True,
    "disabled": False,
    "editable": True,
    "readonly": False,
    "pointerEvents": "auto",
    "offscreen": False,
    "coveredBy": None,
    "rect": {"x": 10, "y": 10, "w": 80, "h": 20},
}


def _ev(v: Verdict, prefix: str) -> list[str]:
    return [e for e in v.evidence if e.startswith(prefix)]


# ----------------------------------------------------------------------------- the five verdicts
def test_empty_delta_is_no_op_with_click_hint():
    v = verdict_for(delta(), target=TARGET)
    assert v.verdict == "no_op"
    assert v.evidence == ["no_observable_change"]
    assert v.hint and "click was delivered to button#go" in v.hint and "Re-observe" in v.hint


def test_dom_change_is_changed():
    v = verdict_for(
        delta(
            dom_changed=True,
            nodes_changed_total=2,
            nodes_modified=[{"path": "span#n", "before": "a", "after": "b"}],
        )
    )
    assert v.verdict == "changed"
    assert _ev(v, "dom_changed: 2 nodes (+0 -0 ~1 listed)")
    assert v.hint is None


def test_url_change_is_navigated():
    v = verdict_for(delta(url_changed=True, url_after=URL + "?next"))
    assert v.verdict == "navigated" and _ev(v, "url_changed: ")


def test_new_tab_is_navigated_with_switch_hint():
    v = verdict_for(delta(new_tabs=["http://127.0.0.1:1/other.html"]))
    assert v.verdict == "navigated"
    assert _ev(v, "new_tab_opened: ")
    assert v.hint and "switch to it" in v.hint


def test_reload_to_the_same_url_is_navigated_via_cross_document_event():
    v = verdict_for(delta(navigations=[URL], cross_document_navigations=[URL]))
    assert v.verdict == "navigated"
    assert _ev(v, "navigation_events: 1 (cross-document: 1)")


def test_fragment_only_url_change_is_changed_not_navigated():
    v = verdict_for(delta(url_only_fragment=True, url_after=URL + "#sec", navigations=[URL + "#sec"]))
    assert v.verdict == "changed"
    assert _ev(v, "url_fragment_changed: ") and _ev(v, "navigation_events")


def test_history_event_without_url_change_is_no_op_with_history_evidence():
    v = verdict_for(delta(navigations=[URL]), target=TARGET)
    assert v.verdict == "no_op"
    assert _ev(v, "history_changed_same_document: 1 event(s)")


@pytest.mark.parametrize(
    "aborted,phrase", [("page_closed", "tab was closed"), ("page_crashed", "tab crashed")]
)
def test_aborted_settlement_is_unknown_naming_the_cause(aborted, phrase):
    v = verdict_for(
        delta(dom_changed=True, nodes_changed_total=1), settlement=settle(aborted=aborted, settled_by=aborted)
    )
    assert v.verdict == "unknown" and v.evidence[0] == aborted and phrase in v.hint


def test_page_crashed_in_delta_is_unknown():
    v = verdict_for(delta(page_crashed=True))
    assert v.verdict == "unknown" and v.evidence[0] == "page_crashed"


def test_failed_capture_is_unknown():
    v = verdict_for(delta(captures_ok=False))
    assert v.verdict == "unknown" and v.evidence[0] == "capture_failed" and "re-observe" in v.hint


def test_timeout_without_change_is_unknown_and_timeout_with_change_is_changed():
    to = settle(timed_out=True, busy=["dom"], elapsed_ms=8000.0, settled_by="dom")
    v = verdict_for(delta(), settlement=to)
    assert v.verdict == "unknown"
    assert v.evidence[0].startswith("never_settled: still busy ['dom'] after 8000ms (timeout 8000ms)")
    v = verdict_for(delta(dom_changed=True, nodes_changed_total=1), settlement=to)
    assert v.verdict == "changed" and v.evidence[0].startswith("never_settled")
    assert "never went quiet" in v.hint


# ----------------------------------------------------------------------------- blocked
@pytest.mark.parametrize(
    "field,value,token,hint_words",
    [
        ("disabled", True, "target_disabled: button#go", "disabled"),
        ("visible", False, "target_not_visible: button#go", "not rendered"),
        ("offscreen", True, "target_offscreen: button#go rect=", "outside the document"),
        ("pointerEvents", "none", "target_pointer_events_none: button#go", "pointer-events:none"),
    ],
)
def test_preflight_findings_block_a_click(field, value, token, hint_words):
    v = verdict_for(delta(), target={**TARGET, field: value})
    assert v.verdict == "blocked", v
    assert v.evidence[0].startswith(token), v.evidence
    assert hint_words in v.hint


def test_covered_target_names_the_cover():
    v = verdict_for(
        delta(),
        target={**TARGET, "coveredBy": {"path": "div#banner", "tag": "div", "text": "Accept cookies"}},
    )
    assert v.verdict == "blocked"
    assert v.evidence[0] == "target_covered_by: div#banner <div> Accept cookies"
    assert "another element receives the pointer" in v.hint


def test_pointer_events_none_blocks_click_and_hover_but_not_type():
    tgt = {**TARGET, "pointerEvents": "none"}
    assert verdict_for(delta(), action="hover", target=tgt).verdict == "blocked"
    assert verdict_for(delta(), action="type", target=tgt).verdict == "no_op"


def test_readonly_target_blocks_type_only():
    tgt = {**TARGET, "editable": False, "readonly": True}
    v = verdict_for(delta(), action="type", target=tgt)
    assert v.verdict == "blocked" and v.evidence[0].startswith(
        "target_not_editable: button#go (readonly=True"
    )
    assert "readonly" in v.hint
    assert verdict_for(delta(), action="click", target=tgt).verdict == "no_op"


@pytest.mark.parametrize("marker", BLOCK_MARKERS)
def test_every_block_marker_in_a_dispatch_error_blocks(marker):
    v = verdict_for(delta(), dispatch_error=f"TimeoutError: locator.click: {marker}")
    assert v.verdict == "blocked" and v.evidence[0].startswith("dispatch_error: TimeoutError")


def test_missing_select_option_has_its_own_hint():
    v = verdict_for(delta(), action="select", dispatch_error="RuntimeError: did not find some options: 'xx'")
    assert v.verdict == "blocked" and "snapshot()" in v.hint


def test_non_actionability_dispatch_error_without_change_is_unknown():
    v = verdict_for(delta(), dispatch_error="TimeoutError: Timeout 3000ms exceeded")
    assert v.verdict == "unknown" and v.evidence[0].startswith("dispatch_error: TimeoutError")
    assert "raised before it could be delivered" in v.hint


def test_dispatch_error_with_a_real_change_is_changed_and_says_both():
    v = verdict_for(
        delta(dom_changed=True, nodes_changed_total=1), dispatch_error="TimeoutError: Timeout 3000ms exceeded"
    )
    assert v.verdict == "changed"
    assert v.evidence[0].startswith("dispatch_error_but_state_changed: TimeoutError: Timeout 3000ms")
    # an actionability error keeps its pre-flight item first, then the both-happened item
    v = verdict_for(
        delta(dom_changed=True, nodes_changed_total=1), dispatch_error="Error: intercepts pointer events"
    )
    assert v.verdict == "changed"
    assert v.evidence[0].startswith("dispatch_error: Error: intercepts pointer events")
    assert _ev(v, "dispatch_error_but_state_changed: Error: intercepts pointer events")


def test_blocked_looking_target_that_still_changed_is_changed_with_cover_hint():
    v = verdict_for(
        delta(dom_changed=True, nodes_changed_total=1),
        target={**TARGET, "coveredBy": {"path": "div#c", "tag": "div", "text": ""}},
    )
    assert v.verdict == "changed" and v.evidence[0].startswith("target_covered_by")
    assert "came from the covering element" in v.hint


# ----------------------------------------------------------------------------- side effects that are not effects
def test_focus_move_is_not_an_effect_for_click_but_is_for_press_and_wrap():
    d = delta(focus_changed=True, focus_before=None, focus_after="input#q")
    v = verdict_for(d, target=TARGET)
    assert v.verdict == "no_op" and _ev(v, "focus_moved_only: None -> input#q")
    assert not _ev(v, "focus_changed")
    assert verdict_for(d, action="press", args={"key": "Tab"}).verdict == "changed"
    assert verdict_for(d, action="external-noop", args={"mode": "wrap"}).verdict == "changed"
    assert verdict_for(d, action="wrap", args={"mode": "wrap"}).verdict == "changed"


def test_dispatcher_scroll_into_view_is_not_an_effect_for_click():
    d = delta(scroll_dy=400, screenshot_changed=True, screenshot_changed_fraction=0.4, screenshot_hamming=20)
    v = verdict_for(d, target={**TARGET, "inViewport": False})
    assert v.verdict == "no_op"
    assert _ev(v, "scrolled_into_view_only: dx=0 dy=400")
    assert not _ev(v, "window_scrolled") and not _ev(v, "screenshot_changed")
    # the same delta is the effect of a scroll action
    assert verdict_for(d, action="scroll", args={"direction": "down"}).verdict == "changed"


def test_covered_target_retries_count_as_dispatcher_scroll():
    d = delta(scroll_dy=120)
    assert _dispatcher_scrolled(d, "click", TARGET, "Error: intercepts pointer events")
    assert not _dispatcher_scrolled(d, "click", TARGET, None)
    assert not _dispatcher_scrolled(d, "scroll", {**TARGET, "inViewport": False}, None)
    assert not _dispatcher_scrolled(d, "external", {**TARGET, "inViewport": False}, None)


def test_prescroll_is_stated_as_evidence_but_not_counted():
    v = verdict_for(delta(), target={**TARGET, "prescroll": {"scrolled": True, "dx": 0, "dy": 350}})
    assert v.verdict == "no_op"
    assert _ev(v, "scrolled_into_view_before_capture: dx=0 dy=350")


def test_container_and_frame_scrolls_are_effects():
    v = verdict_for(
        delta(container_scroll={"path": "div#panel", "before": 0, "after": 300, "delta": 300}),
        action="scroll",
        args={"direction": "down", "selector": "#panel"},
    )
    assert v.verdict == "changed" and _ev(v, "container_scrolled: div#panel 0 -> 300")
    v = verdict_for(
        delta(frame_scrolls=[{"path": "iframe#f", "before": 0, "after": 200, "delta": 200, "dx": 0}]),
        action="scroll",
        args={"direction": "down"},
    )
    assert v.verdict == "changed" and _ev(v, "frame_scrolled: iframe#f 0 -> 200")


def test_window_scroll_is_the_effect_of_a_scroll_action():
    v = verdict_for(delta(scroll_dy=600), action="scroll", args={"direction": "down"})
    assert v.verdict == "changed" and _ev(v, "window_scrolled: dx=0 dy=600")


# ----------------------------------------------------------------------------- screenshot rules
def test_screenshot_below_the_perceptual_floor_is_weak_and_no_op():
    d = delta(screenshot_changed=True, screenshot_changed_fraction=0.002, screenshot_hamming=0)
    assert not _visual_is_perceptual(d)
    v = verdict_for(d, action="press", args={"key": "Enter"})
    assert v.verdict == "no_op"
    assert _ev(v, "screenshot_changed_weak: 0.002 of pixels, dhash distance 0")
    assert not _ev(v, "screenshot_changed:")


@pytest.mark.parametrize("fraction,hamming", [(0.002, 1), (0.006, 0), (0.3, 12)])
def test_perceptual_screenshot_change_alone_is_changed(fraction, hamming):
    d = delta(screenshot_changed=True, screenshot_changed_fraction=fraction, screenshot_hamming=hamming)
    assert _visual_is_perceptual(d)
    v = verdict_for(d)
    assert v.verdict == "changed" and _ev(
        v, f"screenshot_changed: {fraction:.3f} of pixels, dhash distance {hamming}"
    )


def test_screenshot_with_focus_move_needs_the_big_visual_threshold():
    small = delta(
        focus_changed=True,
        focus_after="button#go",
        screenshot_changed=True,
        screenshot_changed_fraction=0.01,
        screenshot_hamming=2,
    )
    assert verdict_for(small).verdict == "no_op"
    big = delta(
        focus_changed=True,
        focus_after="button#go",
        screenshot_changed=True,
        screenshot_changed_fraction=0.05,
        screenshot_hamming=2,
    )
    assert verdict_for(big).verdict == "changed"


def test_masked_regions_are_mentioned_in_screenshot_evidence():
    d = delta(
        screenshot_changed=True,
        screenshot_changed_fraction=0.1,
        screenshot_hamming=5,
        screenshot_masked_regions=2,
    )
    assert _ev(
        verdict_for(d), "screenshot_changed: 0.100 of pixels, dhash distance 5, 2 background region(s) masked"
    )


# ----------------------------------------------------------------------------- other effects and their hints
def test_value_change_is_changed():
    v = verdict_for(
        delta(value_changed=True, value_before="", value_after="ada"),
        action="type",
        args={"selector": "#n", "text": "ada"},
    )
    assert v.verdict == "changed" and _ev(v, "value_changed: '' -> 'ada'")


def test_modal_opened_has_interact_hint():
    v = verdict_for(delta(modals_opened=["div#dlg"]))
    assert (
        v.verdict == "changed"
        and _ev(v, "modal_opened: ['div#dlg']")
        and "interact with it or close it" in v.hint
    )


def test_modal_closed_is_changed():
    v = verdict_for(delta(modals_closed=["div#dlg"]))
    assert v.verdict == "changed" and _ev(v, "modal_closed: ")


def test_js_dialog_hints_how_it_was_handled():
    v = verdict_for(delta(dialogs=[{"type": "alert", "message": "hi", "handled": "accept"}]))
    assert v.verdict == "changed" and _ev(v, "js_dialog: ['alert:hi']")
    assert v.hint == "a JavaScript alert was shown and accepted automatically"
    v = verdict_for(delta(dialogs=[{"type": "beforeunload", "message": "", "handled": "dismiss"}]))
    assert "navigation was cancelled" in v.hint


def test_beforeunload_accepted_on_a_navigation():
    v = verdict_for(
        delta(
            url_changed=True,
            url_after=URL + "2",
            dialogs=[{"type": "beforeunload", "message": "", "handled": "accept"}],
        )
    )
    assert v.verdict == "navigated" and "(beforeunload)" in v.hint and "accepted automatically" in v.hint


def test_download_is_changed_with_do_not_retry_hint():
    v = verdict_for(delta(downloads=[{"filename": "report.bin", "url": URL + "/file.bin"}]))
    assert v.verdict == "changed" and _ev(v, "download_started: report.bin (")
    assert "do not retry the click" in v.hint


def test_http_error_hints_for_changed_and_navigated():
    err = {"url": URL + "/submit", "status": 400, "method": "POST"}
    v = verdict_for(delta(dom_changed=True, nodes_changed_total=1, http_errors=[err]))
    assert v.verdict == "changed" and _ev(v, "http_error: 400 POST ") and "returned HTTP 400" in v.hint
    v = verdict_for(delta(url_changed=True, url_after=URL + "/submit", http_errors=[err]))
    assert v.verdict == "navigated" and "error response, not the intended destination" in v.hint


def test_title_change_and_console_errors_are_evidence():
    v = verdict_for(delta(title_changed=True, console_errors=["boom"]))
    assert v.verdict == "changed" and _ev(v, "title_changed") and _ev(v, "console_errors: 1")


def test_text_change_is_attributable_only_without_background_nodes():
    assert verdict_for(delta(text_changed=True)).verdict == "changed"
    v = verdict_for(
        delta(
            text_changed=True,
            nodes_background_total=1,
            nodes_background=["span#clock"],
            background_roots=["span#clock"],
        ),
        target=TARGET,
    )
    assert v.verdict == "no_op"
    assert _ev(v, "text_changed (not attributable: background nodes were mutating)")
    assert _ev(
        v, "background_mutations_excluded: 1 node(s) already mutating before the action ['span#clock']"
    )
    assert "were set aside" in v.hint and "would not be reported" in v.hint


def test_truncated_fingerprint_is_evidence_and_a_hint_caveat():
    v = verdict_for(delta(fingerprint_truncated={"fingerprinted": 20000, "total": 25000}), target=TARGET)
    assert v.verdict == "no_op"
    assert _ev(v, "fingerprint_truncated: 20000 of 25000 elements fingerprinted")
    assert "fingerprint was truncated" in v.hint


def test_long_timers_are_reported_not_awaited():
    v = verdict_for(
        delta(),
        settlement=settle(
            timers={
                "reason": None,
                "busy_until_ms": None,
                "peak_pending": 0,
                "max_delay_ms": None,
                "long": [30000.0],
                "background": 0,
                "intervals_started": 0,
            }
        ),
        target=TARGET,
    )
    assert _ev(v, "long_timers_not_awaited: setTimeout delays [30000]ms exceed timer_wait_ms 5000")


def test_no_op_click_with_mutations_says_the_handler_ran():
    v = verdict_for(
        delta(),
        settlement=settle(
            dom={"mutations": 3, "background_mutations": 0, "frames_observed": 1, "busy_until_ms": 40.0}
        ),
        target=TARGET,
    )
    assert v.verdict == "no_op"
    assert _ev(v, "dom_mutated_without_fingerprint_change: 3 mutations observed")
    assert "the handler did run" in v.hint


def test_no_op_click_on_a_select_points_at_the_select_tool():
    v = verdict_for(delta(), target={**TARGET, "tag": "select", "path": "select#lang"})
    assert "use select(selector, value)" in v.hint


@pytest.mark.parametrize(
    "action,args,phrase",
    [
        ("hover", {"selector": "#m"}, "hover produced no observable change"),
        ("select", {"selector": "#s", "value": "x"}, "value did not change"),
        ("type", {"selector": "#i", "text": "x"}, "may be readonly"),
        ("fill", {"selector": "#i", "text": "x"}, "may be readonly"),
        ("press", {"key": "Enter"}, "key 'Enter' produced no observable change"),
        ("external", {"mode": "wrap"}, "nothing observable changed; re-observe"),
    ],
)
def test_no_op_hint_is_action_specific(action, args, phrase):
    v = verdict_for(delta(), action=action, args=args)
    assert v.verdict == "no_op" and phrase in v.hint


# ----------------------------------------------------------------------------- scroll hints
def test_scroll_hint_overflow_hidden_lists_containers():
    hint = _scroll_hint(
        delta(),
        {**SCROLL, "overflowHidden": True},
        [{"path": "div#panel", "scrollTop": 0, "scrollHeight": 1000, "clientHeight": 400}],
        None,
        "down",
    )
    assert (
        "overflow:hidden" in hint
        and "div#panel (0/600)" in hint
        and "scroll(selector=<one of these>)" in hint
    )


@pytest.mark.parametrize(
    "scroll,direction,phrase",
    [
        ({**SCROLL, "pageScrollable": False}, "down", "document itself is not scrollable"),
        ({**SCROLL, "atBottom": True}, "down", "window is already at the bottom"),
        ({**SCROLL, "atTop": True}, "up", "window is already at the top"),
    ],
)
def test_scroll_hint_page_geometry(scroll, direction, phrase):
    assert phrase in _scroll_hint(delta(), scroll, [], None, direction)


def test_scroll_hint_for_targets():
    at_bottom = {
        "path": "div#p",
        "selfScrollable": True,
        "scrollTop": 600,
        "clientHeight": 400,
        "scrollHeight": 1000,
    }
    assert "div#p is already at the bottom" in _scroll_hint(delta(), SCROLL, [], at_bottom, "down")
    assert "div#p is already at the top" in _scroll_hint(
        delta(), SCROLL, [], {**at_bottom, "scrollTop": 0}, "up"
    )
    anc = {
        "path": "span#t",
        "scrollableAncestor": {"path": "div#p", "scrollTop": 10, "scrollHeight": 1000, "clientHeight": 400},
    }
    h = _scroll_hint(delta(), SCROLL, [], anc, "down")
    assert "scrollable ancestor is div#p (scrollTop 10/600)" in h and "try scroll(selector='div#p')" in h
    none = {"path": "span#t"}
    assert "no scrollable ancestor" in _scroll_hint(delta(), SCROLL, [], none, "down")
    assert "already at the bottom" not in _scroll_hint(delta(), SCROLL, [], {"error": "x"}, "down")


def test_scroll_hint_for_frames():
    fs_bottom = {
        "path": "span#t",
        "frameScroll": {"atBottom": True, "atTop": False, "scrollHeight": 900, "clientHeight": 300},
    }
    assert "frame is already scrolled to the bottom" in _scroll_hint(delta(), SCROLL, [], fs_bottom, "down")
    fs_top = {
        "path": "span#t",
        "frameScroll": {"atBottom": False, "atTop": True, "scrollHeight": 900, "clientHeight": 300},
    }
    assert "frame is already at the top" in _scroll_hint(delta(), SCROLL, [], fs_top, "up")
    fs_flat = {
        "path": "span#t",
        "frameScroll": {"atBottom": False, "atTop": False, "scrollHeight": 300, "clientHeight": 300},
    }
    assert "frame document is not scrollable" in _scroll_hint(delta(), SCROLL, [], fs_flat, "down")
    frames = [{"path": "iframe#f", "scrollable": True, "y": 0, "scrollHeight": 900, "clientHeight": 300}]
    h = _scroll_hint(delta(), {**SCROLL, "pageScrollable": False}, [], None, "down", frames)
    assert "scrollable same-origin iframes: iframe#f (0/600)" in h and "frame='iframe#f'" in h


def test_scroll_hint_fallback_when_nothing_explains_it():
    assert "virtualised scrolling" in _scroll_hint(delta(), SCROLL, [], None, "down")


# ----------------------------------------------------------------------------- form validation (F10)
def test_browser_validation_refusal_is_blocked_with_form_validation_evidence():
    d = delta(
        validation_blocked=[
            {
                "path": "input#phone",
                "tag": "input",
                "id": "phone",
                "name": None,
                "type": "tel",
                "message": "Please fill out this field.",
                "flags": ["valueMissing"],
            }
        ],
        focus_changed=True,
        focus_after="input#phone",
        screenshot_changed=True,
        screenshot_changed_fraction=0.007,
        screenshot_hamming=0,
    )
    assert not substantive_change(d, "click")
    v = verdict_for(d, target=TARGET)
    assert v.verdict == "blocked", v
    assert v.evidence[0].startswith("form_validation_blocked: 1 control(s)")
    assert "input#phone (valueMissing: " in v.evidence[0] and "Please fill out this field." in v.evidence[0]
    assert _ev(v, "focus_moved_only") and _ev(v, "screenshot_changed_weak")
    assert "form validation blocked the submit" in v.hint and "fill that field and retry" in v.hint


# ----------------------------------------------------------------------------- coverage of the table
EVIDENCE_TOKENS = {
    "no_observable_change",
    "focus_moved_only",
    "scrolled_into_view_only",
    "scrolled_into_view_before_capture",
    "screenshot_changed_weak",
    "dom_mutated_without_fingerprint_change",
    "history_changed_same_document",
    "background_mutations_excluded",
    "long_timers_not_awaited",
    "url_changed",
    "url_fragment_changed",
    "navigation_events",
    "new_tab_opened",
    "download_started",
    "http_error",
    "title_changed",
    "window_scrolled",
    "container_scrolled",
    "frame_scrolled",
    "value_changed",
    "focus_changed",
    "modal_opened",
    "modal_closed",
    "js_dialog",
    "dom_changed",
    "text_changed",
    "screenshot_changed",
    "console_errors",
    "fingerprint_truncated",
    "target_disabled",
    "target_covered_by",
    "target_not_visible",
    "target_pointer_events_none",
    "target_offscreen",
    "target_not_editable",
    "dispatch_error",
    "dispatch_error_but_state_changed",
    "page_closed",
    "page_crashed",
    "capture_failed",
    "never_settled",
    "form_validation_blocked",
}


def _token(e: str) -> str:
    return e.split(":")[0].split(" (")[0].strip()


def test_every_documented_evidence_token_is_produced_by_the_table():
    """The receipt vocabulary the README documents is exactly what the decision table can emit."""
    seen: set[str] = set()
    receipts = [
        verdict_for(delta(), target=TARGET),
        verdict_for(delta(focus_changed=True, focus_after="a"), target=TARGET),
        verdict_for(
            delta(scroll_dy=1),
            target={**TARGET, "inViewport": False, "prescroll": {"scrolled": True, "dx": 0, "dy": 1}},
        ),
        verdict_for(
            delta(screenshot_changed=True, screenshot_changed_fraction=0.002, screenshot_hamming=0),
            target=TARGET,
        ),
        verdict_for(
            delta(navigations=[URL]),
            settlement=settle(
                dom={"mutations": 1, "background_mutations": 0, "frames_observed": 1, "busy_until_ms": 1.0}
            ),
            target=TARGET,
        ),
        verdict_for(
            delta(
                text_changed=True,
                nodes_background_total=1,
                nodes_background=["x"],
                background_roots=["x"],
                fingerprint_truncated={"fingerprinted": 1, "total": 2},
            ),
            settlement=settle(
                timers={
                    "reason": None,
                    "busy_until_ms": None,
                    "peak_pending": 0,
                    "max_delay_ms": None,
                    "long": [9000.0],
                    "background": 0,
                    "intervals_started": 0,
                }
            ),
            target=TARGET,
        ),
        verdict_for(
            delta(
                url_changed=True,
                url_after=URL + "2",
                navigations=[URL + "2"],
                cross_document_navigations=[URL + "2"],
                new_tabs=["t"],
                downloads=[{"filename": "f", "url": "u"}],
                http_errors=[{"url": "u", "status": 500, "method": "GET"}],
                title_changed=True,
            )
        ),
        verdict_for(
            delta(
                url_only_fragment=True,
                url_after=URL + "#a",
                scroll_dy=5,
                container_scroll={"path": "p", "before": 0, "after": 1, "delta": 1},
                frame_scrolls=[{"path": "f", "before": 0, "after": 1, "delta": 1, "dx": 0}],
                value_changed=True,
                modals_opened=["m"],
                modals_closed=["n"],
                dialogs=[{"type": "alert", "message": "m", "handled": "accept"}],
                dom_changed=True,
                nodes_changed_total=1,
                text_changed=True,
                focus_changed=True,
                screenshot_changed=True,
                screenshot_changed_fraction=0.5,
                screenshot_hamming=9,
                console_errors=["e"],
            ),
            action="press",
            args={"key": "Enter"},
        ),
        verdict_for(
            delta(),
            target={
                **TARGET,
                "disabled": True,
                "coveredBy": {"path": "c", "tag": "div", "text": ""},
                "visible": False,
                "pointerEvents": "none",
                "offscreen": True,
            },
            dispatch_error="x: element is disabled",
        ),
        verdict_for(delta(), action="type", target={**TARGET, "editable": False}),
        verdict_for(
            delta(dom_changed=True, nodes_changed_total=1), dispatch_error="x: intercepts pointer events"
        ),
        verdict_for(delta(), settlement=settle(aborted="page_closed", settled_by="page_closed")),
        verdict_for(
            delta(page_crashed=True), settlement=settle(aborted="page_crashed", settled_by="page_crashed")
        ),
        verdict_for(delta(captures_ok=False)),
        verdict_for(
            delta(),
            settlement=settle(timed_out=True, busy=["network"], elapsed_ms=8000.0, settled_by="network"),
        ),
        verdict_for(
            delta(
                validation_blocked=[
                    {
                        "path": "input#p",
                        "tag": "input",
                        "id": "p",
                        "name": None,
                        "type": "tel",
                        "message": "m",
                        "flags": ["valueMissing"],
                    }
                ]
            ),
            target=TARGET,
        ),
    ]
    for v in receipts:
        assert v.verdict in VERDICTS
        seen |= {_token(e) for e in v.evidence}
    assert seen >= EVIDENCE_TOKENS, sorted(EVIDENCE_TOKENS - seen)
    assert seen <= EVIDENCE_TOKENS, sorted(seen - EVIDENCE_TOKENS)


def test_every_verdict_has_evidence_and_no_op_blocked_have_hints():
    cases = {
        "changed": verdict_for(delta(dom_changed=True, nodes_changed_total=1)),
        "no_op": verdict_for(delta(), target=TARGET),
        "navigated": verdict_for(delta(url_changed=True, url_after=URL + "2")),
        "blocked": verdict_for(delta(), target={**TARGET, "disabled": True}),
        "unknown": verdict_for(delta(captures_ok=False)),
    }
    assert set(cases) == set(VERDICTS)
    for name, v in cases.items():
        assert v.verdict == name and v.evidence and all(e.strip() for e in v.evidence)
        if name in ("no_op", "blocked"):
            assert v.hint
        assert v.to_dict() == {"verdict": name, "evidence": v.evidence, "hint": v.hint}


def test_collect_evidence_on_an_empty_delta_is_empty():
    assert _collect_evidence(Delta()) == []
