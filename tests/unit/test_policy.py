"""The done gate as a pure function: last verdict + refusal counter -> decision. No browser,
no server - this is the decision table in ``docs/DESIGN.md`` §4b, row by row."""

from __future__ import annotations

import pytest

from action_receipt.policy import (
    DEFAULT_MAX_REFUSALS,
    EFFECTIVE,
    GENERIC_HINT,
    NO_ACTION_HINT,
    NON_EFFECTIVE,
    DoneDecision,
    DonePolicy,
    decide_done,
    refusal_reason,
)
from action_receipt.verdict import VERDICTS

ON = DonePolicy(enforce=True)
OFF = DonePolicy(enforce=False)


def test_the_policy_partitions_every_verdict_the_library_can_emit():
    # Every verdict is either effective or not; nothing falls through to the "unrecognised" branch.
    assert set(EFFECTIVE) | set(NON_EFFECTIVE) == set(VERDICTS)
    assert not set(EFFECTIVE) & set(NON_EFFECTIVE)
    assert DEFAULT_MAX_REFUSALS == 2 and DonePolicy().max_refusals == 2 and not DonePolicy().enforce


@pytest.mark.parametrize("verdict", EFFECTIVE)
def test_effective_verdicts_are_accepted_when_enforced(verdict):
    d = decide_done(verdict, 0, ON)
    assert d == DoneDecision(True, False, None, None, 0, 2, True)
    assert d.instruction is None and d.to_dict()["instruction"] is None


@pytest.mark.parametrize("verdict", NON_EFFECTIVE)
def test_non_effective_verdicts_are_refused_with_the_receipts_hint(verdict):
    d = decide_done(verdict, 0, ON, last_hint="try scroll(selector=#panel)")
    assert not d.accepted and not d.overridden
    assert d.reason == f"the last action's receipt verdict was {verdict}"
    assert d.hint == "try scroll(selector=#panel)"
    assert d.refusals == 1 and d.max_refusals == 2 and d.enforced
    assert d.instruction is not None and "(1/2)" in d.instruction
    # a receipt without a hint gets the generic one, never None
    assert decide_done(verdict, 0, ON).hint == GENERIC_HINT


def test_no_action_yet_is_refused_when_enforced():
    d = decide_done(None, 0, ON, last_hint="ignored: there is no receipt")
    assert not d.accepted and d.reason == "no action has been performed in this session"
    assert d.hint == NO_ACTION_HINT and d.refusals == 1


def test_the_counter_climbs_then_the_claim_is_accepted_as_overridden():
    d1 = decide_done("no_op", 0, ON)
    d2 = decide_done("no_op", d1.refusals, ON)
    d3 = decide_done("no_op", d2.refusals, ON)
    assert (d1.accepted, d1.refusals) == (False, 1)
    assert (d2.accepted, d2.refusals) == (False, 2)
    assert (d3.accepted, d3.overridden, d3.refusals) == (True, True, 2)
    # the override still says why it would have refused, so the caller can log it
    assert d3.reason == "the last action's receipt verdict was no_op" and d3.hint == GENERIC_HINT
    assert d3.instruction is None


@pytest.mark.parametrize("verdict", [None, *NON_EFFECTIVE, *EFFECTIVE, "weird"])
def test_enforcement_off_accepts_everything_and_never_counts(verdict):
    d = decide_done(verdict, 5, OFF)
    assert d.accepted and not d.overridden and d.reason is None and d.hint is None
    assert d.refusals == 5 and not d.enforced


def test_max_refusals_zero_means_never_refuse_but_still_flag_the_override():
    d = decide_done("blocked", 0, DonePolicy(enforce=True, max_refusals=0))
    assert d.accepted and d.overridden and d.refusals == 0 and "blocked" in (d.reason or "")


def test_an_unknown_verdict_is_not_evidence_of_an_effect():
    assert refusal_reason("weak_visual") == (
        "the last action's receipt verdict was 'weak_visual', which is not changed or navigated"
    )
    assert refusal_reason("changed") is None and refusal_reason(None)


def test_decision_and_policy_serialise_to_plain_dicts():
    assert DonePolicy(enforce=True, max_refusals=3).to_dict() == {"enforce": True, "max_refusals": 3}
    d = decide_done("unknown", 1, DonePolicy(enforce=True, max_refusals=3)).to_dict()
    assert set(d) == {
        "accepted",
        "overridden",
        "reason",
        "hint",
        "refusals",
        "max_refusals",
        "enforced",
        "instruction",
    }
    assert d["refusals"] == 2 and "(2/3)" in d["instruction"]
