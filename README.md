# action-receipt

[![CI](https://github.com/JeremiahM37/action-receipt/actions/workflows/ci.yml/badge.svg)](https://github.com/JeremiahM37/action-receipt/actions/workflows/ci.yml)
[![CodeQL](https://github.com/JeremiahM37/action-receipt/actions/workflows/codeql.yml/badge.svg)](https://github.com/JeremiahM37/action-receipt/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11 | 3.12 | 3.13](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](pyproject.toml)

A deterministic effect oracle for browser agents, shipped as an MCP server. Every action
returns a **receipt**: *dispatch → settlement → delta → verdict + recovery hint*. No model is
involved anywhere in the loop — the receipt is a pure function of the live page.

```
verdict:  no_op
evidence: [no_observable_change]
hint:     the document has overflow:hidden, so wheel/keyboard scrolling of the window is
          disabled; scrollable containers on the page: div#panel (0/450); try
          scroll(selector=<one of these>)
settlement: settled_by=quiet_window elapsed=88ms (network 0 pending, dom 0 late mutations)
```

## Why

Agents are told an action succeeded when nothing happened, and then plan the next step from a
false premise. This is a shape that recurs across unrelated codebases, not one project's bug
(all verified in source on 2026-09-02 — see [`docs/PREMISE.md`](docs/PREMISE.md) for file,
line and commit):

1. **browser-use** — the default `scroll` (`ScrollAction.pages = 1.0`) returns a non-error
   `ActionResult` with memory text `Scrolled down 1000px` even when the page could not move;
   `completed_scrolls` is never consulted and nothing in the dispatch chain reads the scroll
   position ([#5361](https://github.com/browser-use/browser-use/issues/5361),
   [#5438](https://github.com/browser-use/browser-use/issues/5438), and since then
   [#5486](https://github.com/browser-use/browser-use/issues/5486) — all open, nine fix PRs
   open, none merged, no maintainer reply).
2. **UI-TARS-desktop** — the ADB operator returns `{status: 'success'}` before un-awaited
   swipe/scroll/type promises settle, so the next screenshot is taken before the gesture
   lands ([#1952](https://github.com/bytedance/UI-TARS-desktop/issues/1952), open). Same
   consequence, different stage: that one is a *settlement* failure.
3. **Skyvern** — [PR #3080](https://github.com/Skyvern-AI/skyvern/pull/3080) (merged
   2025-08-04) renders `skipped` actions with success styling in the UI.

And the "wait for the UI to be ready" step is a magic constant everywhere: Anthropic's
computer-use reference sleeps **2.0 s** before every screenshot, OSWorld's runner defaults to
**0.0** and its Claude runner to **0.5**, playwright-mcp brackets its network wait with two
**500 ms** sleeps, browser-use waits **0.1 s** between actions. None is measured. The receipt
replaces the constant with a measurement: which signal settled, and how long it took.

## Install

Not on PyPI yet; install from the repository (Python ≥ 3.11):

```bash
pip install git+https://github.com/JeremiahM37/action-receipt          # into the current environment
pipx install git+https://github.com/JeremiahM37/action-receipt         # or as an isolated CLI: `action-receipt`
playwright install chromium                                             # once, if no Chromium is cached yet
```

Pin a release with `@v0.2.0` after the URL. For a local checkout:

```bash
git clone https://github.com/JeremiahM37/action-receipt && cd action-receipt
python3 -m venv .venv && . .venv/bin/activate
make install            # pip install -e '.[dev]' + playwright install chromium
make test-unit          # < 5 s, no browser; `make test` runs every tier (~3 min)
```

Tested with Playwright 1.62 / Chromium 151 and `mcp` 2.1.

## MCP configuration

Claude Code (`claude mcp add`) or `~/.claude.json`:

```json
{
  "mcpServers": {
    "action-receipt": {
      "command": "/home/you/.venvs/action-receipt/bin/action-receipt",
      "args": []
    }
  }
}
```

Claude Desktop (`claude_desktop_config.json`) uses the same block. Options:

| flag / env | effect |
|---|---|
| `--cdp ws://127.0.0.1:9222/devtools/browser/<id>` or `--cdp http://127.0.0.1:9222` (`AR_CDP`) | attach to a running Chromium instead of launching one; required for **wrap mode** around another tool's tabs |
| `--cdp-new-context` | with `--cdp`: work in an isolated incognito context, never touching existing tabs (wrap mode then only sees our own tabs) |
| `--cdp-listen PORT` (`AR_CDP_LISTEN`) | launch our own Chromium with a remote-debugging port on `127.0.0.1` and work in its default context, so another tool attached to `http://127.0.0.1:PORT` (playwright-mcp `--cdp-endpoint`) drives the same tabs; the browser starts with the server. Exclusive with `--cdp` |
| `--headed` | launch a visible Chromium |
| `--enforce-done` (`AR_ENFORCE_DONE=1`) | refuse `done` while the last receipt is `no_op` / `blocked` / `unknown` or no action has been performed — see *Enforcing the receipt* |
| `--max-refusals N` (`AR_MAX_REFUSALS`, default 2) | with `--enforce-done`: accept `done` anyway (`overridden: true`) after N refusals of the same claim |
| `AR_QUIET_MS` (default 100) | length of the quiet window settlement must observe |
| `AR_SETTLE_TIMEOUT_MS` (default 10000) | hard cap on settlement |
| `AR_BODY_GRACE_MS` (default 1500) | how long a response whose headers arrived but whose body never finishes keeps the network signal busy (pages routinely never read a fetch body; Chromium never reports such a request finished) |

## Running beside playwright-mcp

Already driving the browser with [`@playwright/mcp`](https://github.com/microsoft/playwright-mcp)?
Keep doing so. `action-receipt --cdp-listen 9222` launches the Chromium and publishes its
remote-debugging port; `@playwright/mcp --cdp-endpoint http://127.0.0.1:9222` attaches to the same
browser and the same tabs, and each of its actions gets a receipt through `receipt_begin` /
`receipt_end`. The config block for Claude Code and Claude Desktop, the agent-side protocol, the
limits and a runnable example are in [`docs/using-with-playwright-mcp.md`](docs/using-with-playwright-mcp.md)
and `examples/playwright-mcp/`. Verified with `@playwright/mcp@0.0.80`
(`tests/e2e/test_playwright_mcp.py` runs the flow against the pinned package).

## Tools

Every action tool returns `{"result": ..., "receipt": {...}}`. Anything that prevents a
receipt (no tab open yet, `receipt_end` without `receipt_begin`, the browser gone) comes back as
`{"error": {"type", "message"}, "receipt": null}` — a structured result, never a protocol error.
Every element-targeting tool takes `frame=<iframe selector>` to act inside a same-origin iframe.

| tool | what it does |
|---|---|
| `open(url)` | new tab at `url`, made current |
| `navigate(url)` | current tab → `url` |
| `click(selector, button="left", click_count=1, frame=None)` | click first match of a Playwright selector (CSS, `text=`, `role=button[name=…]`); `click_count=2` double-clicks |
| `hover(selector, frame=None)` | move the pointer over the element (hover menus); the receipt says whether anything appeared |
| `select(selector, value, frame=None)` | choose a `<select>` option by value (falls back to label); a missing option is a cheap `blocked` |
| `type(selector, text, clear=False, submit=False, frame=None)` | type key-by-key (`clear=True` replaces; `submit=True` presses Enter after). Works on `contenteditable`. Password values are masked in the receipt |
| `press(key, selector=None, frame=None)` | key press on element or page (Tab / Enter / Escape …); a focus move counts as an effect here |
| `scroll(direction="down", pages=1.0, selector=None, frame=None)` | real wheel gesture of `pages` viewport-heights, over `selector` if given |
| `snapshot(aria=True)` | url, title, scroll geometry, focus, modals, scrollable containers, frames, tabs, aria tree |
| `tabs(select=None)` | list tabs / switch current |
| `receipt_last(n=1)` | last n receipts |
| `receipt_schema()` | JSON Schema of the receipt (also `action_receipt.RECEIPT_JSON_SCHEMA`; `validate_receipt()` checks a dict against it and the invariants) |
| `receipt_begin(label, selector=None, page_url=None, frame=None)` / `receipt_end()` | **wrap mode**: capture before, do the action with *any other tool* (playwright-mcp, raw CDP, a human), capture after — same receipt |
| `done(summary, claimed_effects=None)` | end the task. With `--enforce-done` it is **refused** (`accepted: false`, with the last receipt's `hint`) while the last receipt is `no_op` / `blocked` / `unknown` or nothing has been done; accepted after `changed` / `navigated`; accepted with `overridden: true` after `--max-refusals`. Without the flag it always accepts |
| `receipt_policy()` | the gate's policy and state: `enforce`, `max_refusals`, `refusals` since the last accepted claim, the last verdict |
| `browser_info()` | how the browser can be shared: the CDP endpoint (`cdp_url`, set with `--cdp-listen` or `--cdp`), the mode, the open tabs |

## Receipt schema

Taken verbatim from a run (scroll on a page whose body has `overflow:hidden`; only `#panel`
scrolls). Node lists and images are omitted from `before`/`after`; the delta carries the
bounded changed-node list.

```json
{
  "id": "adb965690218",
  "action": {"name": "scroll", "args": {"direction": "down", "pages": 1.0, "selector": null}},
  "verdict": "no_op",
  "evidence": ["no_observable_change"],
  "hint": "the document has overflow:hidden, so wheel/keyboard scrolling of the window is disabled; scrollable containers on the page: div#panel (0/450); try scroll(selector=<one of these>)",
  "dispatch": {"ok": true, "error": null, "elapsed_ms": 17.6, "preflight": null, "pre_settle": {"waited_ms": 0.4}},
  "settlement": {
    "settled": true, "elapsed_ms": 88.0, "settled_by": "quiet_window", "timed_out": false, "aborted": null,
    "busy_at_timeout": [],
    "signals": {
      "network":    {"peak_pending": 0, "started_total": 1, "reason": null, "busy_until_ms": null, "released": []},
      "dom":        {"mutations": 0, "background_mutations": 0, "frames_observed": 1, "busy_until_ms": null},
      "scroll":     {"events": 0, "busy_until_ms": null},
      "layout":     {"shifts": 0, "busy_until_ms": null},
      "animations": {"reason": null, "busy_until_ms": null, "infinite": 0, "background": 0},
      "timers":     {"pending": 0, "long": [], "intervals_started": 0, "busy_until_ms": null},
      "document":   {"reason": null, "busy_until_ms": null},
      "navigation": {"count": 0, "last_url": null},
      "quiet_ms": 100.0, "timeout_ms": 10000.0, "body_grace_ms": 1500.0, "timer_wait_ms": 5000.0
    },
    "polls": 5
  },
  "delta": {
    "url_changed": false, "url_before": "…/inner-scroll.html", "url_after": "…/inner-scroll.html", "url_only_fragment": false,
    "title_changed": false,
    "scroll_dx": 0, "scroll_dy": 0, "container_scroll": null, "frame_scrolls": [],
    "focus_changed": false, "focus_before": null, "focus_after": null,
    "dom_changed": false, "element_count_delta": 0, "text_changed": false,
    "nodes_added": [], "nodes_removed": [], "nodes_modified": [], "nodes_changed_total": 0,
    "nodes_background": [], "nodes_background_total": 0, "background_roots": [], "fingerprint_truncated": null,
    "value_before": null, "value_after": null, "value_changed": false,
    "modals_opened": [], "modals_closed": [],
    "screenshot_hamming": 0, "screenshot_changed_fraction": 0.0, "screenshot_changed": false, "screenshot_masked_regions": [],
    "dialogs": [], "navigations": [], "cross_document_navigations": [], "new_tabs": [], "downloads": [],
    "http_errors": [], "console_errors": [], "page_crashed": false,
    "captures_ok": true
  },
  "before": {"url": "…", "title": "…", "ready_state": "complete", "scroll": {"x": 0, "y": 0, "scrollHeight": 821, "clientHeight": 800, "pageScrollable": false, "overflowHidden": true, "atTop": true, "atBottom": false},
             "focused": null, "modals": [], "element_count": 16, "element_total": 16, "truncated": false, "frames": [], "background": [],
             "dom_hash": "f4ed4588ad336ff8", "text_hash": 1234, "screenshot_dhash": "…", "target": null, "capture_ms": 57.7, "error": null},
  "after":  {"…": "same shape"},
  "timing_ms": {"capture_before": 57.7, "dispatch": 17.6, "settle": 88.0, "capture_after": 71.9, "observed_window": 180.2, "total": 237.6}
}
```

`timing_ms.observed_window` is the span from dispatch start to the end of the after-capture:
the window in which an effect could have been seen. An effect that lands later than that is
not in the receipt — and the receipt tells you exactly how long it looked. The full contract
is `action_receipt.RECEIPT_JSON_SCHEMA` (also the `receipt_schema` tool); every receipt the
test suite produces is validated against it.

When a `selector` is given, `dispatch.preflight` (and `before.target` / `after.target`) carry
the target's `disabled`, `readonly`, `editable`, `visible`, `inViewport`, `offscreen`,
`pointerEvents`, `rect`, `coveredBy` (the element that actually receives the pointer at the
target's centre, if it is not the target), `scrollableAncestor`, `inFrame` / `frameScroll`
for targets inside an iframe, and its `value` (masked for passwords). Node paths are
`tag#id` / `tag:nth-of-type(i)` chains; `#shadow-root` marks a shadow boundary and
`#document` an iframe boundary (`iframe#f>#document>html>body:nth-of-type(1)>span#n`).

## Verdicts

| verdict | when | typical evidence | hint |
|---|---|---|---|
| `changed` | something observable changed that is not a navigation | `dom_changed`, `window_scrolled`, `container_scrolled`, `frame_scrolled`, `value_changed`, `modal_opened`, `js_dialog`, `download_started`, `http_error`, `screenshot_changed`, `text_changed` | modal / dialog / download / HTTP-error guidance; a warning when the page never settled |
| `no_op` | dispatch succeeded, settlement observed quiet, no substantive change | `no_observable_change`, `focus_moved_only`, `scrolled_into_view_only`, `scrolled_into_view_before_capture`, `screenshot_changed_weak`, `dom_mutated_without_fingerprint_change`, `history_changed_same_document`, `background_mutations_excluded`, `long_timers_not_awaited` | action-specific: which container/frame to scroll, "already at the bottom", the observed window for a click, "field may be readonly", which nodes were set aside as background |
| `navigated` | main-frame URL changed (not fragment-only), a cross-document navigation happened (a main-frame document request + `framenavigated`; `pushState` to a new URL counts via `url_changed`, `replaceState` on the same URL does not), or a new tab opened | `url_changed`, `navigation_events`, `new_tab_opened`, `http_error` (4xx/5xx destination), `js_dialog` (`beforeunload`) | which tab to switch to; that the destination was an HTTP error |
| `blocked` | pre-flight found the target disabled / covered / invisible / `pointer-events:none` / off the document / not editable, the option is not in the select, or Playwright's actionability check refused, **and** nothing changed; **or** the browser's constraint validation refused the submit (`required`, `pattern`, `type=email`, `min`… — an `invalid` event fired on a control) and nothing else changed: the validation bubble is not in the DOM, so this is the only trace of it | `target_disabled`, `target_covered_by: header`, `target_not_visible`, `target_pointer_events_none`, `target_offscreen`, `target_not_editable`, `dispatch_error`, `form_validation_blocked: 1 control(s) … input#phone (valueMissing: "Please fill out this field.")` | what is in the way and what to do about it; for validation, the field and the browser's own message: `form validation blocked the submit: input#phone (valueMissing: "…"); fill that field and retry` |
| `unknown` | the tab closed or crashed, a capture failed, or settlement never settled (timed out) with nothing observed changed | `page_closed`, `page_crashed`, `capture_failed`, `never_settled: still busy [...]` | open a new tab / re-observe before retrying |

Things that are reported but do **not** count as an effect, because they are side effects of
*attempting* the action rather than the page's response: focus moving (and its focus ring) for
`click`/`type`/`scroll`/`hover`/`select` — it does count for `press` and wrap-mode receipts;
the scroll Playwright makes to bring an off-viewport target into view (or its retries against a
covered one) — reported as `scrolled_into_view_only`; and mutations inside **background
nodes** — elements that were already mutating repeatedly before the action (a ticker, a live
feed), reported as `background_mutations_excluded` and listed in `delta.nodes_background`.
Background nodes also do not keep settlement busy, so a page with a clock settles instead of
timing out. The cost is stated in the hint: an effect *inside* a background node is not reported.
Two more things are excluded by construction rather than by rule: a target that is not fully in
the viewport is scrolled into view *before* the before-capture (`scrolled_into_view_before_capture`),
so that scroll never enters the delta while a real repaint on the target still does; and a
screenshot-only difference below the perceptual floor (dhash distance 0 and < 0.5 % of pixels — a
focus-ring flicker, sub-pixel anti-aliasing) is reported as `screenshot_changed_weak` and does not
make a `changed`. Timers the action scheduled are awaited up to `timer_wait_ms` (5 s); longer ones
are listed under `long_timers_not_awaited` so the reader knows what was not observed. And when the
browser's constraint validation refuses a submit (`delta.validation_blocked`, from the `invalid`
events it fires), the focus jump to the invalid control, the scroll that reveals it and the
validation bubble — which is painted outside the DOM — are the refusal, not an effect: the
verdict is `blocked` and the hint names the field and the browser's message.

## Enforcing the receipt

The receipt tells the agent that an action had no effect; nothing so far stops the agent from
claiming the task is done anyway. `--enforce-done` (`AR_ENFORCE_DONE=1`) turns the `done` tool
into a deterministic gate:

```
done(summary="saved the profile")
→ {"accepted": false, "overridden": false,
   "reason": "the last action's receipt verdict was blocked",
   "hint": "form validation blocked the submit: input#phone (valueMissing: \"Please fill out this field.\"); fill that field and retry",
   "instruction": "done was refused (1/2): the task is not complete while the last action had no effect. …",
   "refusals": 1, "max_refusals": 2, "enforced": true,
   "last_receipt": {"id": "…", "action": "click", "verdict": "blocked", "evidence": ["form_validation_blocked: …"], "hint": "…"}}
```

- **Refused** while the last receipt's verdict is `no_op`, `blocked` or `unknown`, or no action
  has been performed in the session (there is nothing to be done about). The refusal carries the
  last receipt's own hint — the same computed hint the action returned.
- **Accepted** as soon as the last receipt is `changed` or `navigated`.
- After `--max-refusals` (default 2) refusals of the same claim it is accepted with
  `overridden: true`, so an agent can never be trapped; the counter starts over with each
  accepted claim. `receipt_policy()` reports the policy, the live counter and the last verdict.
- Without the flag `done` always accepts (`enforced: false`) and still reports the last receipt,
  so the tool is safe to expose in every manifest.
- No model is involved. The decision is a pure function of the last verdict and a counter
  (`action_receipt.policy.decide_done`; the table is in `docs/DESIGN.md` §4b). Wrap-mode
  receipts count like any other, so the gate works beside playwright-mcp too.

**What it measured.** This is the benchmark's `receipt_enforced` arm (`bench/agent_loop_v2.py`,
`bench/results/agent_loop_v2.md`) shipped in the server. On the 36 trap tasks, P(claims DONE |
validator fail) went **100 % → 20 %** (10/10 → 1/5) for qwen3.6:35b-a3b and **67 % → 56 %**
(14/21 → 9/16) for qwen3.5:4b, with task success 86 % → 93 % and 71 % → 78 %. Since 0.3.0 the
arm runs through the server's own `done` tool (`--gate server`, the default; `--gate harness`
reproduces the published run with the original in-harness policy).

**Two limits, both by construction:**

1. **A false DONE after a `changed` action passes the gate.** The gate reads one receipt, not
   the task: an agent that toggled a setting and never clicked *Apply* ends on a `changed` receipt
   and is let through. The benchmark's counterfactual bounds this — of the false DONEs in the arms
   *without* the gate, 80 % (8/10, 35B) and 57 % (8/14, 4B) ended on a receipt the gate would
   have refused; the rest would have passed it.
2. **Refusals can be spurious** — the task was already complete and the last action merely had no
   effect. The benchmark reads the validator at every refusal: in the published run the 35B was
   refused once, and that refusal was *justified* (the validator was failing at that moment; the
   episode ended passing — *rescued*), with **0 spurious**; the 4B was refused **0 times** — it
   never claimed done right after a no-effect action, which is why its number barely moved. Expect
   spurious refusals on real tasks; the cap bounds their cost to `max_refusals` extra steps.

## What it deliberately does not do

- **No LLM judge.** MLLM verifiers have a measured *agreement bias* — "a strong tendency to
  over-validate agent behavior … pervasive, resilient to test-time scaling"
  ([arXiv 2507.11662](https://arxiv.org/abs/2507.11662), *Let's Think in Two Steps: Mitigating
  Agreement Bias in MLLMs with Self-Grounded Verification*). A verifier that agrees with the
  agent is the failure this project exists to remove.
- **No task-level judgement.** The receipt answers "did *this action* do anything, and what",
  never "is the task done".
- **No desktop path yet.** Linux/AT-SPI is future work (see `docs/DESIGN.md`).

## The numbers

**The problem is measured, not assumed.** Across 91 public OSWorld-Verified leaderboard runs
(34,437 scored task-runs, feasible tasks only), **53.0 % [52.3, 53.6] of the runs the validator
scored as failed end with the agent claiming success** - 28.9 % at a 15-step budget, 59.0 % at
50, 69.0 % at 100. The benchmark's own `evaluate()` never reads the agent's DONE, so nothing in
the leaderboard number sees this. The measurement, its provenance and its limitations are
written up in [`docs/false-completion-on-osworld.md`](docs/false-completion-on-osworld.md).

**What a receipt does about it.** `bench/agent_loop_v2.py` drives an LLM agent through 36
small web tasks whose traps a receipt can see (covered buttons, silent validation, disabled
controls, inner scroll containers, confirm dialogs, optimistic UI that reverts, work that
continues in a new tab), with a programmatic validator per task, in three arms: no receipt,
receipt shown to the agent, receipt shown *and* enforced as a deterministic gate that refuses
`done` while the last receipt is `no_op` / `blocked` / `unknown`. Two local models, two seeds,
432 episodes, library `6d4eae52879e` (`bench/REPORT.md` has every table):

| model | P(claims DONE \| validator fail): no receipt | receipt shown | receipt enforced | task success: no receipt -> enforced |
|---|---|---|---|---|
| qwen3.6:35b-a3b | **100 %** (10/10) | **0 %** (0/4) | 20 % (1/5) | 86 % -> 93 % |
| qwen3.5:4b | 67 % (14/21) | 56 % (9/16) | 56 % (9/16) | 71 % -> 78 % |

Read the denominators: once the receipt is in the loop the strong model barely fails at all,
so its intervals are wide ([0, 49] and [3.6, 62.4]). The honest summary is that receipts remove
a whole class of false completions for a capable model and roughly a third of them for a small
one, and raise task success in both. The trap that survived every earlier version - the
browser's native validation bubble, invisible to the DOM - is now a `blocked` receipt with the
field named, and no longer produces a false DONE in either receipt arm.

The receipt itself is measured too: on effects that land after a fetch, a CSS transition, a
timer or a two-stage fetch-then-render, at delays from 0 to 3 s, the receipt's settlement was
**stale 0 times in 640 trials**, waiting only as long as the effect took; a blind 0.5 s sleep
was stale in 40 % of the fetch trials and a blind 2 s sleep in 15 %. On a labelled corpus of
79 action/page cases the verdict was right **237 of 237** times across three runs. The cost is
about 185 ms per action on a fixture page (33 ms for the bare Playwright click), dominated by
the 100 ms quiet window.

## Status

Prototype, version 0.3.0 (`CHANGELOG.md`). Three test tiers, all hermetic: **unit** (the decision
table, the diff and the schema on synthetic inputs, no browser), **integration** (every action
against local fixture pages served from `tests/fixtures/`, plus CDP attach and crashed/closed
tabs against a Chromium the suite launches itself) and **e2e** (the real MCP server over stdio,
a scripted agent recovering from `no_op` / `blocked` / `navigated` receipts on the bench apps,
wrap mode around a plain Playwright script, the `done` gate, `--cdp-listen`, and `@playwright/mcp`
itself driving the shared browser — that last one skips when `npx` is unavailable). Every receipt every test produces is validated
against the schema. `docs/TESTING.md` lists every integration case and the measured bounds; the
premise check and its caveats are in `docs/PREMISE.md`; the design and its two named risks in
`docs/DESIGN.md`; the benchmark suite and its numbers in `bench/REPORT.md`.

## Development

`CONTRIBUTING.md` covers the test tiers, adding a fixture page, adding a verdict evidence token
(and its schema entry), and reproducing the benchmarks. `make help` lists the targets; each one is
exactly what the matching CI job runs, so `make lint test` is a faithful preview of CI.
Security reports go through GitHub's private advisories (`SECURITY.md`).
