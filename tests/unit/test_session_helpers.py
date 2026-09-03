"""The pure parts of ReceiptSession: error description, the short dispatch timeout for a
target pre-flight already found blocked, receipt serialisation, and the no-page guard."""

from __future__ import annotations

import json

import pytest

from action_receipt.schema import validate_receipt
from action_receipt.session import DEFAULT_ACTION_TIMEOUT_MS, ReceiptSession, _describe_error

from ._synth import receipt


def test_describe_error_keeps_the_first_line_and_surfaces_a_buried_actionability_reason():
    e = TimeoutError(
        "Timeout 3000ms exceeded.\n  waiting for locator('#x')\n    <div> intercepts pointer events\n"
    )
    assert _describe_error(e) == "TimeoutError: Timeout 3000ms exceeded. (intercepts pointer events)"
    assert _describe_error(RuntimeError("element is not visible")) == "RuntimeError: element is not visible"
    assert _describe_error(ValueError("")) == "ValueError: "


@pytest.mark.parametrize(
    "preflight,expected",
    [
        (None, DEFAULT_ACTION_TIMEOUT_MS),
        ({}, DEFAULT_ACTION_TIMEOUT_MS),
        ({"error": "detached"}, DEFAULT_ACTION_TIMEOUT_MS),
        ({"path": "b", "visible": True}, DEFAULT_ACTION_TIMEOUT_MS),
        ({"path": "b", "disabled": True}, 300),
        ({"path": "b", "coveredBy": {"path": "c"}}, 300),
        ({"path": "b", "visible": False}, 300),
        ({"path": "b", "readonly": True}, 300),
        ({"path": "b", "pointerEvents": "none"}, 300),
        ({"path": "b", "offscreen": True}, 300),
    ],
)
def test_dispatch_timeout_is_short_when_preflight_already_saw_a_blocker(preflight, expected):
    s = ReceiptSession()
    s._preflight = preflight
    assert s._dispatch_timeout() == expected


def test_dispatch_timeout_never_exceeds_the_configured_action_timeout():
    s = ReceiptSession(action_timeout_ms=200)
    s._preflight = {"path": "b", "disabled": True}
    assert s._dispatch_timeout() == 200


def test_session_defaults_and_no_page_guard():
    s = ReceiptSession()
    assert s.settle_cfg.quiet_ms == 100.0 and s.screenshots and s.dialog_action == "accept"
    assert s.pages() == [] and s.receipts == [] and s.current is None
    with pytest.raises(RuntimeError, match=r"no current page; call open\(\) first"):
        s._state()


async def test_end_without_begin_is_a_runtime_error():
    s = ReceiptSession()
    with pytest.raises(RuntimeError, match="receipt_end without receipt_begin"):
        await s.end()


def test_receipt_to_dict_is_the_schema_shape_with_rounded_timings():
    r = receipt(
        timing={
            "prescroll": 0.123,
            "pre_settle": 0.44,
            "capture_before": 5.06,
            "dispatch": 12.0,
            "settle": 120.0,
            "capture_after": 5.0,
            "observed_window": 140.05,
            "total": 150.0,
        }
    )
    d = r.to_dict()
    assert d["action"] == {"name": "click", "args": {"selector": "#x"}}
    assert d["timing_ms"]["prescroll"] == 0.1 and d["timing_ms"]["observed_window"] == 140.1
    assert set(d) == {
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
    }
    validate_receipt(d)
    json.dumps(d)


def test_locator_uses_the_frame_locator_when_a_frame_is_given():
    calls: list[tuple] = []

    class Loc:
        def __init__(self, name):
            self.name = name

        @property
        def first(self):
            calls.append(("first", self.name))
            return self

        def locator(self, sel):
            calls.append(("locator", sel))
            return Loc(f"{self.name}>{sel}")

    class Page:
        def locator(self, sel):
            calls.append(("page.locator", sel))
            return Loc(sel)

        def frame_locator(self, sel):
            calls.append(("frame_locator", sel))
            return Loc(f"frame:{sel}")

    ReceiptSession._locator(Page(), "#x", None)
    assert calls == [("page.locator", "#x"), ("first", "#x")]
    calls.clear()
    ReceiptSession._locator(Page(), "#x", "iframe#f")
    assert calls == [
        ("frame_locator", "iframe#f"),
        ("first", "frame:iframe#f"),
        ("locator", "#x"),
        ("first", "frame:iframe#f>#x"),
    ]
