"""Verdict + evidence + recovery hint, derived from the delta by a fixed decision table.

Order of evaluation (first match wins):

1. unknown    - the page closed or crashed, a capture failed, or settlement never settled
                (timed out) with nothing observed changed.
2. blocked    - dispatch raised an actionability error (covered / disabled / detached /
                not visible / option missing) and nothing changed; or pre-flight found the
                target disabled or covered and nothing changed; or the browser's constraint
                validation refused the submit (an ``invalid`` event fired on a control) and
                nothing else changed - the validation bubble, the focus move to the invalid
                control and the scroll that brings it into view are the refusal, not an effect.
3. navigated  - main-frame URL changed (not fragment-only), a main-frame navigation event
                fired, or a new tab opened.
4. changed    - any other delta field is non-empty.
5. no_op      - dispatch succeeded, settlement observed quiet, no delta at all.

Every verdict carries at least one evidence item. ``no_op`` and ``blocked`` always carry a hint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .delta import Delta
from .settle import SettleReport

VERDICTS = ("changed", "no_op", "navigated", "blocked", "unknown")

BLOCK_MARKERS = (
    "intercepts pointer events",
    "element is not visible",
    "element is not enabled",
    "element is disabled",
    "not attached",
    "element is outside of the viewport",
    "waiting for element to be visible, enabled",
    "element is not editable",
    "element is readonly",
    "did not find some options",
)


@dataclass
class Verdict:
    verdict: str
    evidence: list[str] = field(default_factory=list)
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "evidence": self.evidence, "hint": self.hint}


def _validation_label(v: dict[str, Any]) -> str:
    """``input#phone (valueMissing: "Please fill out this field.")`` - the control as the agent can
    select it, the validity flag(s) that are set and the browser's own message."""
    path = v.get("path") or v.get("tag") or "control"
    if v.get("name") and "#" not in path.split(">")[-1]:
        path += f" [name={v['name']}]"
    flags = "+".join(v.get("flags") or []) or "invalid"
    msg = (v.get("message") or "").strip().replace('"', "'")
    return f'{path} ({flags}: "{msg}")' if msg else f"{path} ({flags})"


def _validation_hint(d: Delta, *, changed: bool) -> str:
    items = ", ".join(_validation_label(v) for v in d.validation_blocked[:3])
    n = len(d.validation_blocked)
    more = f" (+{n - 3} more)" if n > 3 else ""
    fields = "that field" if n == 1 else "those fields"
    verb = (
        "fill"
        if all((v.get("flags") or ["valueMissing"]) == ["valueMissing"] for v in d.validation_blocked)
        else "correct"
    )
    if changed:
        return (
            f"form validation blocked the submit: {items}{more}; the listed changes are the page's response to "
            f"the invalid input, not the effect of a submit - {verb} {fields} and retry"
        )
    return f"form validation blocked the submit: {items}{more}; {verb} {fields} and retry"


def _collect_evidence(d: Delta) -> list[str]:
    ev: list[str] = []
    if d.validation_blocked:
        # First: it is the one item that explains everything else in this receipt.
        n = len(d.validation_blocked)
        ev.append(
            f"form_validation_blocked: {n} control(s) rejected by the browser's constraint validation: "
            + ", ".join(_validation_label(v) for v in d.validation_blocked[:3])
            + (f", +{n - 3} more" if n > 3 else "")
        )
    if d.url_changed:
        ev.append(f"url_changed: {d.url_before} -> {d.url_after}")
    elif d.url_only_fragment:
        ev.append(f"url_fragment_changed: {d.url_after}")
    if d.navigations and (d.cross_document_navigations or d.url_changed or d.url_only_fragment):
        ev.append(
            f"navigation_events: {len(d.navigations)} (cross-document: {len(d.cross_document_navigations)})"
        )
    elif d.navigations:
        ev.append(
            f"history_changed_same_document: {len(d.navigations)} event(s), url unchanged (pushState/replaceState)"
        )
    if d.new_tabs:
        ev.append(f"new_tab_opened: {d.new_tabs}")
    if d.downloads:
        ev.append(
            "download_started: "
            + ", ".join(f"{x.get('filename') or '?'} ({x.get('url') or '?'})" for x in d.downloads[:3])
        )
    if d.http_errors:
        ev.append(
            "http_error: "
            + ", ".join(
                f"{x['status']} {x.get('method') or ''} {x['url']}".replace("  ", " ")
                for x in d.http_errors[:3]
            )
        )
    if d.title_changed:
        ev.append("title_changed")
    if d.scroll_dy or d.scroll_dx:
        ev.append(f"window_scrolled: dx={d.scroll_dx} dy={d.scroll_dy}")
    if d.container_scroll and d.container_scroll.get("delta"):
        cs = d.container_scroll
        ev.append(f"container_scrolled: {cs['path']} {cs['before']} -> {cs['after']}")
    for fs in d.frame_scrolls[:3]:
        ev.append(f"frame_scrolled: {fs['path']} {fs['before']} -> {fs['after']}")
    if d.value_changed:
        ev.append(f"value_changed: {d.value_before!r} -> {d.value_after!r}")
    if d.focus_changed:
        ev.append(f"focus_changed: {d.focus_before} -> {d.focus_after}")
    if d.modals_opened:
        ev.append(f"modal_opened: {d.modals_opened}")
    if d.modals_closed:
        ev.append(f"modal_closed: {d.modals_closed}")
    if d.dialogs:
        ev.append(f"js_dialog: {[x['type'] + ':' + x['message'][:60] for x in d.dialogs]}")
    if d.dom_changed:
        ev.append(
            f"dom_changed: {d.nodes_changed_total} nodes "
            f"(+{len(d.nodes_added)} -{len(d.nodes_removed)} ~{len(d.nodes_modified)} listed)"
        )
    if d.text_changed and d.nodes_background_total:
        ev.append("text_changed (not attributable: background nodes were mutating)")
    elif d.text_changed:
        ev.append("text_changed")
    if d.nodes_background_total:
        ev.append(
            f"background_mutations_excluded: {d.nodes_background_total} node(s) already mutating before the action "
            f"{d.nodes_background[:3]}"
        )
    if d.screenshot_changed:
        masked = (
            f", {d.screenshot_masked_regions} background region(s) masked"
            if d.screenshot_masked_regions
            else ""
        )
        ev.append(
            f"screenshot_changed: {d.screenshot_changed_fraction:.3f} of pixels, dhash distance {d.screenshot_hamming}{masked}"
        )
    if d.console_errors:
        ev.append(f"console_errors: {len(d.console_errors)}")
    if d.fingerprint_truncated:
        ft = d.fingerprint_truncated
        ev.append(
            f"fingerprint_truncated: {ft['fingerprinted']} of {ft['total']} elements fingerprinted; "
            "changes beyond the cap are invisible to the DOM delta (screenshot/text measures still apply)"
        )
    return ev


def _scroll_hint(
    d: Delta,
    before_scroll: dict[str, Any],
    containers: list[dict[str, Any]],
    target: dict[str, Any] | None,
    direction: str,
    frames: list[dict[str, Any]] | None = None,
) -> str:
    parts = []
    if target and "error" not in target:
        anc = target.get("scrollableAncestor")
        fs = target.get("frameScroll")
        if target.get("selfScrollable"):
            if direction == "down" and target.get("scrollTop", 0) + target.get(
                "clientHeight", 0
            ) >= target.get("scrollHeight", 0):
                parts.append(f"target {target['path']} is already at the bottom")
            elif direction == "up" and target.get("scrollTop", 0) <= 0:
                parts.append(f"target {target['path']} is already at the top")
        elif anc:
            parts.append(
                f"target is not scrollable itself; its scrollable ancestor is {anc['path']} "
                f"(scrollTop {anc['scrollTop']}/{anc['scrollHeight'] - anc['clientHeight']})"
            )
            parts.append(f"try scroll(selector='{anc['path']}')")
        elif fs:
            if direction == "down" and fs.get("atBottom"):
                parts.append("the target's frame is already scrolled to the bottom")
            elif direction == "up" and fs.get("atTop"):
                parts.append("the target's frame is already at the top")
            elif fs.get("scrollHeight", 0) <= fs.get("clientHeight", 0) + 1:
                parts.append("the target's frame document is not scrollable (scrollHeight == clientHeight)")
        else:
            parts.append("target element is not scrollable and has no scrollable ancestor")
    if before_scroll.get("overflowHidden"):
        parts.append(
            "the document has overflow:hidden, so wheel/keyboard scrolling of the window is disabled"
        )
    elif not before_scroll.get("pageScrollable", True):
        parts.append("the document itself is not scrollable (scrollHeight == clientHeight)")
    elif direction == "down" and before_scroll.get("atBottom"):
        parts.append("the window is already at the bottom")
    elif direction == "up" and before_scroll.get("atTop"):
        parts.append("the window is already at the top")
    if containers:
        cands = ", ".join(
            f"{c['path']} ({c['scrollTop']}/{c['scrollHeight'] - c['clientHeight']})" for c in containers[:3]
        )
        parts.append(f"scrollable containers on the page: {cands}; try scroll(selector=<one of these>)")
    scrollable_frames = [f for f in (frames or []) if f.get("scrollable")]
    if scrollable_frames:
        cands = ", ".join(
            f"{f['path']} ({f['y']}/{f['scrollHeight'] - f['clientHeight']})" for f in scrollable_frames[:3]
        )
        parts.append(
            f"scrollable same-origin iframes: {cands}; try scroll(selector=<element in it>, frame='{scrollable_frames[0]['path']}')"
        )
    if not parts:
        parts.append(
            "no scroll motion observed; the scroll target may be a nested container or the page may use "
            "virtualised scrolling"
        )
    return "; ".join(parts)


# Focus moving (and the focus ring it paints) is a side effect of *attempting* most actions,
# not their effect. Only for key presses and wrapped/external actions does a focus move count.
_FOCUS_IS_EFFECT = ("press", "external", "wrap")
# A screenshot change this large without any DOM change is real (canvas, video, CSS-only state).
_BIG_VISUAL = 0.02
# A screenshot-only change needs a perceptual signal to count as an effect: a dHash distance
# of at least one bit, or a changed-pixel fraction clear of the noise floor. A button's
# :active / focus-ring repaint on a key press lands at 0.002 with dhash 0 (bench F9) - the
# same category as the focus move the design already excludes. It is still reported.
_SUBSTANTIVE_VISUAL_FRACTION = 0.005
_SUBSTANTIVE_VISUAL_HAMMING = 1


def _visual_is_perceptual(d: Delta) -> bool:
    return bool(
        d.screenshot_changed
        and (
            (d.screenshot_changed_fraction or 0) >= _SUBSTANTIVE_VISUAL_FRACTION
            or (d.screenshot_hamming or 0) >= _SUBSTANTIVE_VISUAL_HAMMING
        )
    )


def _scrolled(d: Delta) -> bool:
    return bool(
        d.scroll_dx
        or d.scroll_dy
        or (d.container_scroll and d.container_scroll.get("delta"))
        or d.frame_scrolls
    )


def _dispatcher_scrolled(
    d: Delta, action: str, target_before: dict[str, Any] | None, dispatch_error: str | None
) -> bool:
    """True when the only scroll in the delta is one the *dispatcher* made: Playwright scrolls an
    off-viewport target into view before clicking/typing, and on a covered target its retries
    try other scroll alignments. Neither is the action's effect."""
    if not _scrolled(d) or action in ("scroll", "press") or action.startswith(("external", "wrap")):
        return False
    tb = target_before or {}
    blocked_err = dispatch_error is not None and any(m in dispatch_error.lower() for m in BLOCK_MARKERS)
    return tb.get("inViewport") is False or blocked_err


def substantive_change(
    d: Delta, action: str, *, target_before: dict[str, Any] | None = None, dispatch_error: str | None = None
) -> bool:
    core = any(
        [
            d.url_changed,
            d.url_only_fragment,
            d.cross_document_navigations,
            d.new_tabs,
            d.title_changed,
            d.value_changed,
            d.modals_opened,
            d.modals_closed,
            d.dialogs,
            d.downloads,
            d.dom_changed,
            d.text_changed and not d.nodes_background_total,
        ]
    )
    if core:
        return True
    if d.validation_blocked:
        # The browser refused the submit. What is left in the delta - focus moving to the first
        # invalid control, the scroll that brings it into view, the bubble it paints (bench F10:
        # 0.7% of pixels, dhash 0, no DOM trace) - is the refusal itself, not an effect.
        return False
    if _scrolled(d):
        # A scroll (and the screenshot change it brings) is the effect unless the dispatcher made it.
        return not _dispatcher_scrolled(d, action, target_before, dispatch_error)
    if d.focus_changed and (action in _FOCUS_IS_EFFECT or action.startswith("external")):
        return True
    return _visual_is_perceptual(d) and (
        not d.focus_changed or (d.screenshot_changed_fraction or 0) >= _BIG_VISUAL
    )


def decide(
    d: Delta,
    *,
    action: str,
    args: dict[str, Any],
    dispatch_error: str | None,
    settlement: SettleReport,
    before_scroll: dict[str, Any],
    containers: list[dict[str, Any]],
    target_before: dict[str, Any] | None,
    frames: list[dict[str, Any]] | None = None,
) -> Verdict:
    ev = _collect_evidence(d)
    changed = substantive_change(d, action, target_before=target_before, dispatch_error=dispatch_error)
    ps = (target_before or {}).get("prescroll") or {}
    if ps.get("scrolled"):
        # Stated so the reader knows the page moved, and why that movement is not in the delta.
        ev.append(
            f"scrolled_into_view_before_capture: dx={ps.get('dx', 0)} dy={ps.get('dy', 0)} (the target was brought "
            "into view before the before-capture, so the dispatcher's scroll is not part of the delta)"
        )
    if not changed and d.validation_blocked:
        # Each side effect of the refusal is still stated, attributed to the validation UI.
        ev = [
            e
            for e in ev
            if not e.startswith(
                (
                    "window_scrolled",
                    "container_scrolled",
                    "frame_scrolled",
                    "screenshot_changed",
                    "focus_changed",
                )
            )
        ]
        if _scrolled(d):
            ev.append(
                f"scrolled_into_view_only: dx={d.scroll_dx} dy={d.scroll_dy} (the browser scrolled the first invalid "
                f"control into view; not counted as an effect for {action})"
            )
        if d.focus_changed:
            ev.append(
                f"focus_moved_only: {d.focus_before} -> {d.focus_after} (focus moved to the first invalid control; "
                f"not counted as an effect for {action})"
            )
        if d.screenshot_changed:
            ev.append(
                f"screenshot_changed_weak: {d.screenshot_changed_fraction:.3f} of pixels, dhash distance {d.screenshot_hamming} "
                f"(the native validation bubble, which is not in the DOM; not counted as an effect for {action})"
            )
    elif not changed and _scrolled(d):
        ev = [
            e
            for e in ev
            if not e.startswith(
                (
                    "window_scrolled",
                    "container_scrolled",
                    "frame_scrolled",
                    "screenshot_changed",
                    "focus_changed",
                )
            )
        ]
        ev.append(
            f"scrolled_into_view_only: dx={d.scroll_dx} dy={d.scroll_dy} (the dispatcher scrolled the target into "
            f"view or retried alignments; not counted as an effect for {action})"
        )
    elif not changed and d.focus_changed:
        ev = [e for e in ev if not e.startswith(("focus_changed", "screenshot_changed"))]
        ev.append(
            f"focus_moved_only: {d.focus_before} -> {d.focus_after} (not counted as an effect for {action})"
        )
    elif not changed and d.screenshot_changed:
        ev = [e for e in ev if not e.startswith("screenshot_changed")]
        ev.append(
            f"screenshot_changed_weak: {d.screenshot_changed_fraction:.3f} of pixels, dhash distance {d.screenshot_hamming} "
            f"(below the perceptual threshold of {_SUBSTANTIVE_VISUAL_FRACTION} / dhash {_SUBSTANTIVE_VISUAL_HAMMING}: "
            f"an :active or focus-ring repaint, not counted as an effect for {action})"
        )

    timeout_ms = settlement.signals.get("timeout_ms", settlement.elapsed_ms)
    never_settled = (
        f"never_settled: still busy {settlement.busy_at_timeout} after {round(settlement.elapsed_ms)}ms "
        f"(timeout {round(timeout_ms)}ms)"
    )
    long_timers = settlement.signals.get("timers", {}).get("long") or []
    if long_timers:
        # The action scheduled work further out than we wait; the reader must know the window closed early.
        ev.append(
            f"long_timers_not_awaited: setTimeout delays {[round(x) for x in long_timers]}ms exceed timer_wait_ms "
            f"{round(settlement.signals.get('timer_wait_ms', 0))}; their effect is outside this receipt's window"
        )

    # 1. unknown
    if settlement.aborted or d.page_crashed:
        why = settlement.aborted or "page_crashed"
        what = "the tab crashed" if "crash" in why else "the tab was closed"
        return Verdict(
            "unknown",
            [why] + ev,
            what + "; nothing more can be observed on it - open a new tab and re-observe before acting again",
        )
    if not d.captures_ok:
        return Verdict(
            "unknown",
            ["capture_failed"] + ev,
            "state could not be captured; re-observe the page before acting again",
        )
    if settlement.timed_out and not changed:
        return Verdict(
            "unknown",
            [never_settled] + ev,
            "page never went quiet and nothing observable changed; the effect may still be pending "
            "or the page has a permanent background activity - re-observe before retrying",
        )
    if settlement.timed_out:
        ev.insert(0, never_settled)

    # 2. blocked
    tb = target_before or {}
    pre_block = []
    if tb.get("disabled"):
        pre_block.append(f"target_disabled: {tb.get('path')}")
    if tb.get("coveredBy"):
        c = tb["coveredBy"]
        pre_block.append(
            f"target_covered_by: {c.get('path')} <{c.get('tag')}> {c.get('text') or ''}".rstrip()
        )
    if tb and tb.get("visible") is False:
        pre_block.append(f"target_not_visible: {tb.get('path')}")
    if tb.get("pointerEvents") == "none" and action in ("click", "hover"):
        pre_block.append(f"target_pointer_events_none: {tb.get('path')}")
    if tb.get("offscreen"):
        pre_block.append(f"target_offscreen: {tb.get('path')} rect={tb.get('rect')}")
    if action in ("type", "fill") and tb and not tb.get("editable", True):
        pre_block.append(
            f"target_not_editable: {tb.get('path')} (readonly={tb.get('readonly')}, disabled={tb.get('disabled')})"
        )
    if dispatch_error and any(m in dispatch_error.lower() for m in BLOCK_MARKERS):
        pre_block.append(f"dispatch_error: {dispatch_error[:200]}")
    if d.validation_blocked and not changed:
        # Dispatch succeeded; the page refused the effect. The evidence already leads with
        # form_validation_blocked and states the bubble / focus / scroll as the refusal's side effects.
        return Verdict("blocked", pre_block + ev, _validation_hint(d, changed=False))
    if pre_block and not changed:
        hint = None
        if any(e.startswith("target_covered_by") for e in pre_block):
            hint = (
                "another element receives the pointer at the target's centre; dismiss or scroll past it, "
                "or click the covering element if that is the intended control"
            )
        elif any(e.startswith("target_disabled") for e in pre_block):
            hint = "the control is disabled; satisfy whatever enables it (fill required fields, wait for load) before clicking"
        elif any(e.startswith("target_not_editable") for e in pre_block):
            hint = "the field is readonly/disabled or not an input; find the editable control or clear readonly state"
        elif any(e.startswith("target_not_visible") for e in pre_block):
            hint = (
                "the element exists but is not rendered; open its container, hover the menu that reveals it, "
                "or scroll it into view first"
            )
        elif any(e.startswith("target_pointer_events_none") for e in pre_block):
            hint = "the element has pointer-events:none, so no click can reach it; the real control is elsewhere (or use keyboard)"
        elif any(e.startswith("target_offscreen") for e in pre_block):
            hint = "the element is positioned outside the document (e.g. left:-9999px); it is not a visible control"
        elif dispatch_error and "did not find some options" in dispatch_error.lower():
            hint = "the requested option is not in the select; read its options from snapshot() and pick one that exists"
        else:
            hint = "the action could not be delivered to the target; re-observe and pick a different element"
        return Verdict("blocked", pre_block + ev, hint)
    if dispatch_error and not changed:
        return Verdict(
            "unknown",
            [f"dispatch_error: {dispatch_error[:200]}"] + ev,
            "the action raised before it could be delivered; re-observe the page",
        )

    # 3. navigated
    if d.url_changed or d.new_tabs or (d.cross_document_navigations and not d.url_only_fragment):
        hint = None
        if d.new_tabs:
            hint = f"a new tab opened at {d.new_tabs[0]}; switch to it if that is where the task continues"
        elif d.http_errors:
            e0 = d.http_errors[0]
            hint = (
                f"the navigation returned HTTP {e0['status']} for {e0['url']}; the page shown is an error "
                "response, not the intended destination"
            )
        elif d.dialogs and d.dialogs[0]["type"] == "beforeunload":
            hint = f"the page asked to confirm leaving (beforeunload) and the prompt was {d.dialogs[0]['handled']}ed automatically"
        return Verdict("navigated", ev, hint)

    # 4. changed
    if changed:
        hint = None
        if dispatch_error:
            ev.insert(0, f"dispatch_error_but_state_changed: {dispatch_error[:160]}")
        if pre_block:
            ev = pre_block + ev
            hint = "state changed although the target looked blocked - the change probably came from the covering element"
        if d.validation_blocked:
            # The page rendered its own error UI (or something else moved) while the browser refused the submit.
            hint = _validation_hint(d, changed=True)
        elif d.dialogs:
            hint = (
                f"a JavaScript {d.dialogs[0]['type']} was shown and {d.dialogs[0]['handled']}ed automatically"
            )
            if d.dialogs[0]["type"] == "beforeunload" and d.dialogs[0]["handled"] == "dismiss":
                hint += "; the navigation was cancelled and the page stayed put"
        elif d.downloads:
            hint = (
                f"a download started ({d.downloads[0].get('filename')}); the page itself did not need to change - "
                "do not retry the click"
            )
        elif d.modals_opened:
            hint = f"a modal opened ({d.modals_opened[0]}); interact with it or close it before continuing"
        elif settlement.timed_out:
            hint = (
                "the page never went quiet (permanent background activity); the listed changes may include "
                "that activity rather than the action's effect - compare the changed nodes against what the action targeted"
            )
        elif d.http_errors:
            e0 = d.http_errors[0]
            hint = f"a request made by this action returned HTTP {e0['status']} ({e0['url']}); check the page for an error message"
        return Verdict("changed", ev, hint)

    # 5. no_op
    mutations = settlement.signals.get("dom", {}).get("mutations", 0)
    if mutations:
        ev.append(
            f"dom_mutated_without_fingerprint_change: {mutations} mutations observed but the DOM fingerprint is identical "
            "(same-content re-render)"
        )
    bg_note = ""
    if d.nodes_background_total or d.background_roots:
        bg_note = (
            f"; {len(d.background_roots)} node(s) were already mutating before the action and were set aside "
            f"({d.background_roots[:3]}) - an effect inside them would not be reported"
        )
    if action == "scroll":
        hint = _scroll_hint(
            d, before_scroll, containers, target_before, args.get("direction", "down"), frames
        )
    elif action == "click":
        tgt = (target_before or {}).get("path", "target")
        hint = (
            f"click was delivered to {tgt} but nothing observable changed within the settle window "
            f"({round(settlement.elapsed_ms)}ms, quiet window {settlement.signals.get('quiet_ms')}ms); "
            "the element may have no handler, need a different event (dblclick/keyboard), or its effect "
            "may be outside the viewport/DOM. Re-observe before retrying the same click"
        )
        if mutations:
            hint += "; the DOM re-rendered to identical content, so the handler did run"
        if (target_before or {}).get("tag") == "select":
            hint += "; this is a <select>: use select(selector, value) or press('ArrowDown', selector=...) to change it"
    elif action == "hover":
        hint = (
            "hover produced no observable change; the element may reveal content on focus or click instead, "
            "or the menu may open with a delay longer than the settle window"
        )
    elif action == "select":
        hint = "the select's value did not change; the option may already have been selected or the control is a custom widget"
    elif action in ("type", "fill"):
        hint = (
            "value did not change; the element may not be the editable control (check for a wrapper div), "
            "may be readonly, or an input mask rejected the text"
        )
    elif action == "press":
        hint = f"key {args.get('key')!r} produced no observable change; check that the intended element has focus"
    else:
        hint = "nothing observable changed; re-observe before repeating the action"
    if d.fingerprint_truncated:
        hint += (
            "; note the DOM fingerprint was truncated, so a change deep in the page could have been missed"
        )
    hint += bg_note
    return Verdict("no_op", ["no_observable_change"] + ev, hint)
