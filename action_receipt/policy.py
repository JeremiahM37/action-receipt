"""The ``done`` gate: a deterministic policy over the last receipt's verdict.

This is the ``receipt_enforced`` arm of ``bench/agent_loop_v2.py`` shipped as a server feature.
An agent ends a task by calling the ``done`` tool; with enforcement on, the server refuses the
call while the last receipt says the last action had no effect (``no_op`` / ``blocked`` /
``unknown``), or while no action has been performed at all, and hands back the receipt's hint.
After ``max_refusals`` refusals the claim is accepted anyway and marked ``overridden``, so an
agent can never be trapped. No model is involved: the decision is a pure function of the last
verdict and a counter, which is what makes it testable on its own (``decide_done``).

The bench measured the policy at 100 % -> 20 % false completion for a 35B model and
67 % -> 56 % for a 4B one (``bench/results/agent_loop_v2.md``), with two limits that are
structural rather than tunable: a false DONE *after* a ``changed`` action passes the gate by
construction, and a refusal can be spurious when the task was already complete.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

EFFECTIVE: tuple[str, ...] = ("changed", "navigated")
NON_EFFECTIVE: tuple[str, ...] = ("no_op", "blocked", "unknown")
DEFAULT_MAX_REFUSALS = 2

NO_ACTION_HINT = (
    "no action has been performed in this session; act on the task, read the receipt of each "
    "action, and call done once one says changed or navigated"
)
GENERIC_HINT = (
    "re-observe the page, act on the part of the task that has not taken effect, and verify "
    "its receipt says changed or navigated"
)


@dataclass(frozen=True)
class DonePolicy:
    enforce: bool = False
    max_refusals: int = DEFAULT_MAX_REFUSALS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DoneDecision:
    accepted: bool
    overridden: bool
    reason: str | None
    hint: str | None
    refusals: int  # refusals counted so far, including this one if it is a refusal
    max_refusals: int
    enforced: bool

    @property
    def instruction(self) -> str | None:
        if self.accepted:
            return None
        return (
            f"done was refused ({self.refusals}/{self.max_refusals}): the task is not complete while "
            "the last action had no effect. Take another action that moves the task forward, confirm "
            "its receipt says changed or navigated, then call done again."
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["instruction"] = self.instruction
        return d


def refusal_reason(last_verdict: str | None) -> str | None:
    """Why a ``done`` should be refused given the last receipt's verdict, or ``None`` to accept.

    ``None`` as the verdict means no receipt exists yet (no action has been performed).
    Any verdict outside ``EFFECTIVE`` is a refusal: the three non-effective verdicts by name,
    and anything unrecognised as a conservative default (a verdict this policy does not know
    is not evidence of an effect).
    """
    if last_verdict is None:
        return "no action has been performed in this session"
    if last_verdict in EFFECTIVE:
        return None
    if last_verdict in NON_EFFECTIVE:
        return f"the last action's receipt verdict was {last_verdict}"
    return f"the last action's receipt verdict was {last_verdict!r}, which is not changed or navigated"


def decide_done(
    last_verdict: str | None,
    refusals: int,
    policy: DonePolicy,
    *,
    last_hint: str | None = None,
) -> DoneDecision:
    """The decision table (also in ``docs/DESIGN.md``):

    | enforce | last verdict            | refusals so far   | decision                          |
    |---------|-------------------------|-------------------|-----------------------------------|
    | off     | any                     | any               | accepted                          |
    | on      | changed / navigated     | any               | accepted                          |
    | on      | none / no_op / blocked / unknown | < max_refusals | refused, refusals + 1     |
    | on      | none / no_op / blocked / unknown | >= max_refusals | accepted, ``overridden`` |

    ``refusals`` is the count *before* this call; the returned decision carries the count after.
    """
    reason = refusal_reason(last_verdict)
    cap, on = policy.max_refusals, policy.enforce
    if not on or reason is None:
        return DoneDecision(True, False, None, None, refusals, cap, on)
    hint = NO_ACTION_HINT if last_verdict is None else (last_hint or GENERIC_HINT)
    if refusals >= cap:
        return DoneDecision(True, True, reason, hint, refusals, cap, on)
    return DoneDecision(False, False, reason, hint, refusals + 1, cap, on)
