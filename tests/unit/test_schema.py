"""The receipt schema, exercised on synthetic receipts: a valid receipt of every verdict passes,
every invariant rejects what it is meant to, the JSON Schema export is self-contained - no browser."""

from __future__ import annotations

import copy
import json

import pytest

from action_receipt.schema import (
    RECEIPT_JSON_SCHEMA,
    DeltaModel,
    ReceiptModel,
    SettledBy,
    SettlementModel,
    VerdictName,
    validate_receipt,
)
from action_receipt.settle import SETTLED_BY_VALUES
from action_receipt.verdict import VERDICTS

from ._synth import URL, capture, delta, receipt, settle, verdict_for

TARGET = {"path": "button#go", "tag": "button", "visible": True, "inViewport": True, "disabled": False}


def _one_of_each() -> dict[str, dict]:
    out = {}
    out["no_op"] = receipt(before=capture(target=TARGET), after=capture(target=TARGET)).to_dict()
    d = delta(
        dom_changed=True,
        nodes_changed_total=1,
        nodes_modified=[{"path": "span#n", "before": "a", "after": "b"}],
    )
    out["changed"] = receipt(d=d, v=verdict_for(d), after=capture(nodes={"span#n": "b"})).to_dict()
    d = delta(
        url_changed=True, url_after=URL + "2", navigations=[URL + "2"], cross_document_navigations=[URL + "2"]
    )
    out["navigated"] = receipt(
        action="navigate", args={"url": URL + "2"}, d=d, v=verdict_for(d, action="navigate")
    ).to_dict()
    d = delta()
    tgt = {**TARGET, "disabled": True}
    out["blocked"] = receipt(
        d=d,
        v=verdict_for(d, target=tgt),
        before=capture(target=tgt),
        after=capture(target=tgt),
        dispatch={
            "ok": False,
            "error": "TimeoutError: element is disabled",
            "elapsed_ms": 300.0,
            "preflight": tgt,
            "pre_settle": None,
        },
    ).to_dict()
    s = settle(aborted="page_closed", settled_by="page_closed")
    d = delta()
    out["unknown"] = receipt(d=d, v=verdict_for(d, settlement=s), settlement=s).to_dict()
    return out


@pytest.mark.parametrize("verdict", VERDICTS)
def test_a_synthetic_receipt_of_every_verdict_validates(verdict):
    d = _one_of_each()[verdict]
    assert d["verdict"] == verdict, d["evidence"]
    m = validate_receipt(d)
    assert isinstance(m, ReceiptModel) and m.verdict == verdict
    json.dumps(d)


def test_wrap_mode_receipt_validates_without_a_dispatch_timing():
    d = delta()
    r = receipt(
        action="external",
        args={"selector": None, "frame": None, "mode": "wrap"},
        d=d,
        v=verdict_for(d, action="external", args={"mode": "wrap"}),
        dispatch={
            "ok": None,
            "error": None,
            "elapsed_ms": None,
            "preflight": None,
            "mode": "wrap",
            "pre_settle": {
                "elapsed_ms": 0.4,
                "polls": 1,
                "quiet": True,
                "waited_for": None,
                "background_roots": 0,
                "max_ms": 800.0,
            },
        },
        timing={
            "pre_settle": 0.4,
            "capture_before": 5.0,
            "settle": 120.0,
            "capture_after": 5.0,
            "observed_window": 140.0,
            "total": 150.0,
        },
    )
    m = validate_receipt(r.to_dict())
    assert m.dispatch.mode == "wrap" and m.dispatch.ok is None


def _broken(good: dict, mutate, match: str | None = None):
    d = copy.deepcopy(good)
    mutate(d)
    with pytest.raises(ValueError, match=match) as ei:
        validate_receipt(d)
    assert not isinstance(ei.value, TypeError)


def test_receipt_level_invariants_reject_drift():
    good = _one_of_each()["no_op"]
    validate_receipt(good)
    _broken(good, lambda d: d["evidence"].clear(), "evidence")
    _broken(good, lambda d: d.update(hint=None), "no_op without a hint")
    _broken(good, lambda d: d.update(hint=""), "no_op without a hint")
    _broken(good, lambda d: d.update(verdict="succeeded"))
    _broken(good, lambda d: d.update(extra_field=1))
    _broken(good, lambda d: d.pop("timing_ms"))
    _broken(good, lambda d: d.update(id="short"))
    _broken(good, lambda d: d["evidence"].append("   "), "empty evidence item")
    _broken(good, lambda d: d["timing_ms"].pop("observed_window"), "observed_window")
    _broken(good, lambda d: d["timing_ms"].pop("dispatch"), "timing_ms.dispatch missing")
    _broken(
        good,
        lambda d: d["settlement"].update(aborted="page_closed", settled=False, settled_by="page_closed"),
        "aborted settlement must yield verdict unknown",
    )
    _broken(
        good,
        lambda d: d["delta"].update(url_changed=True, url_after=URL + "x"),
        "no_op with a substantive delta",
    )
    _broken(
        good,
        lambda d: d["delta"].update(dom_changed=True, nodes_changed_total=1),
        "no_op with a substantive delta",
    )
    _broken(good, lambda d: d.update(verdict="navigated"), "navigated without a navigation")
    _broken(
        good,
        lambda d: d["settlement"].update(
            timed_out=True, settled=False, busy_at_timeout=["dom"], elapsed_ms=8000.0
        ),
        "never_settled",
    )


def test_settlement_invariants():
    good = _one_of_each()["no_op"]
    _broken(good, lambda d: d["settlement"].update(settled_by="magic"))
    _broken(good, lambda d: d["settlement"].update(timed_out=True), "timed_out without busy_at_timeout")
    _broken(
        good,
        lambda d: d["settlement"].update(timed_out=True, busy_at_timeout=["dom"]),
        "timed_out and settled",
    )
    _broken(
        good,
        lambda d: d["settlement"].update(
            timed_out=True, settled=False, busy_at_timeout=["dom"], elapsed_ms=10.0
        ),
        "timed_out before the timeout elapsed",
    )
    _broken(
        good,
        lambda d: d["settlement"].update(aborted="page_closed"),
        "aborted settlement must not be settled",
    )
    _broken(
        good,
        lambda d: d["settlement"].update(aborted="page_closed", settled=False, settled_by="dom"),
        "name the cause",
    )
    _broken(
        good,
        lambda d: d["settlement"]["signals"]["timers"].update(long=[100.0]),
        "listed as long is within timer_wait_ms",
    )
    _broken(good, lambda d: d["settlement"]["signals"]["network"].pop("released"))
    _broken(
        good,
        lambda d: d["settlement"]["signals"]["network"].update(released=[{"url": "u", "reason": "r"}] * 4),
    )
    _broken(good, lambda d: d["settlement"].update(polls=0))
    _broken(good, lambda d: d["settlement"]["signals"].update(quiet_ms=0))


def test_delta_invariants():
    good = _one_of_each()["no_op"]
    _broken(
        good,
        lambda d: d["delta"].update(nodes_modified=[{"path": "x", "before": "a", "after": "b"}]),
        "node lists without dom_changed",
    )
    _broken(good, lambda d: d["delta"].update(url_changed=True, url_only_fragment=True))
    _broken(
        good,
        lambda d: (
            d["delta"]["nodes_added"].extend(["p"] * 13)
            or d["delta"].update(dom_changed=True, nodes_changed_total=13)
        ),
    )
    _broken(
        good,
        lambda d: d["delta"].update(nodes_background=["a"], nodes_background_total=0),
        "more background nodes listed",
    )
    _broken(
        good,
        lambda d: d["delta"].update(cross_document_navigations=["x"], navigations=[]),
        "cross-document navigation without",
    )
    _broken(good, lambda d: d["delta"].update(http_errors=[{"url": "u", "status": 200, "method": "GET"}]))
    _broken(good, lambda d: d["delta"].update(http_errors=[{"url": "u", "status": 500, "method": "GET"}] * 6))
    _broken(good, lambda d: d["delta"].pop("captures_ok"))
    changed = _one_of_each()["changed"]
    _broken(
        changed,
        lambda d: d["delta"].update(nodes_changed_total=0),
        "more nodes listed than nodes_changed_total",
    )


def test_validation_blocked_must_agree_with_its_evidence():
    good = _one_of_each()["no_op"]
    vb = [
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
    _broken(good, lambda d: d["delta"].update(validation_blocked=vb), "must agree")
    _broken(good, lambda d: d["evidence"].append("form_validation_blocked: 1 control(s)"), "must agree")
    # and no_op is never right once validation refused the submit
    _broken(
        good,
        lambda d: (
            d["delta"].update(validation_blocked=vb),
            d["evidence"].append("form_validation_blocked: 1"),
        ),
        "no_op although form validation blocked",
    )


def test_capture_summary_invariants():
    good = _one_of_each()["no_op"]
    _broken(good, lambda d: d["before"].update(truncated=True), "truncated but nothing beyond the cap")
    _broken(good, lambda d: d["before"].update(element_total=0), "element_total < element_count")
    _broken(good, lambda d: d["after"].update(background=["x"] * 51))
    _broken(good, lambda d: d["after"].pop("dom_hash"))


def test_literal_types_stay_in_sync_with_the_runtime_constants():
    assert set(VerdictName.__args__) == set(VERDICTS)
    assert set(SettledBy.__args__) == set(SETTLED_BY_VALUES)
    assert set(SettlementModel.model_fields["settled_by"].annotation.__args__) == set(SETTLED_BY_VALUES)


def test_json_schema_export_is_self_contained_and_strict():
    s = RECEIPT_JSON_SCHEMA
    assert s["title"] == "ReceiptModel" and s.get("additionalProperties") is False
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
        assert k in s["properties"], k
    assert set(s["properties"]["verdict"]["enum"]) == set(VERDICTS)
    assert set(s["required"]) == set(s["properties"])
    json.dumps(s)
    # every nested model is strict too
    for name, sub in s["$defs"].items():
        assert sub.get("additionalProperties") is False, name


def test_delta_model_covers_every_delta_field():
    from action_receipt.delta import Delta

    assert set(DeltaModel.model_fields) == set(Delta().__dict__)
