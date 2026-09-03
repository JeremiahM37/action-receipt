# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Release notes on GitHub are cut from the section
for that version.

## [Unreleased]

## [0.3.0] - 2026-09-03

The benchmark's `receipt_enforced` arm becomes a server feature, and the server learns to share
its browser with `@playwright/mcp`.

### Added

- **The `done` gate.** A `done(summary, claimed_effects=None)` tool ends a task. With
  `--enforce-done` (`AR_ENFORCE_DONE=1`) the server refuses it (`accepted: false`, with the last
  receipt's `hint`, a `reason`, an `instruction` and a `last_receipt` brief) while the last
  receipt's verdict is `no_op` / `blocked` / `unknown` or no action has been performed; after
  `--max-refusals` (`AR_MAX_REFUSALS`, default 2) refusals of the same claim it is accepted with
  `overridden: true`. Without the flag it always accepts. The decision is the pure function
  `action_receipt.policy.decide_done` (`docs/DESIGN.md` §4b) — no model, no browser needed to
  answer. `receipt_policy()` reports the policy, the live refusal count and the last verdict.
  Measured by `bench/agent_loop_v2.py` (false completion 100 % → 20 % for a 35B model, 67 % →
  56 % for a 4B one); the two limits — a false DONE after a `changed` action passes, refusals can
  be spurious — are documented in the README.
- **`--cdp-listen PORT`** (`AR_CDP_LISTEN`): launch our own Chromium with a remote-debugging port
  on `127.0.0.1`, attach to it over CDP and work in its default context, so a second tool pointed
  at the port (playwright-mcp `--cdp-endpoint`) drives the same tabs and wrap mode observes it. The
  browser starts with the server (a lifespan), not on the first tool call, and is closed with it.
- **`browser_info()`**: the shareable CDP endpoint, the mode (`launched` / `attached` / `listen`),
  and the open tabs.
- `docs/using-with-playwright-mcp.md` and `examples/playwright-mcp/` (config for Claude Code and
  Claude Desktop, the agent-side `receipt_begin` → action → `receipt_end` protocol, a Python
  script that drives both servers over stdio through a three-step task and prints the receipts).
- Tests: `tests/unit/test_policy.py` (the decision table), `tests/e2e/test_enforce_done.py`
  (refusal on `no_op` / `blocked`, acceptance after `changed` / `navigated`, override at the cap,
  always-accept without the flag, refusal with no prior action, wrap-mode receipts count),
  `tests/e2e/test_cdp_listen.py`, and `tests/e2e/test_playwright_mcp.py`, which runs the pinned
  `@playwright/mcp@0.0.80` against the shared browser and skips (never fails) without `npx` or
  the registry.

### Changed

- `bench/agent_loop_v2.py` gained `--gate server|harness`: `server` (default) runs the
  `receipt_enforced` arm through the server's own `done` tool with `--enforce-done`; `harness`
  keeps the original in-harness policy so the published results stay reproducible. Every episode
  row records `gate`. The published tables were not re-run.
- The `slow` marker now means "needs something outside the repo" (Ollama, or npx and the npm
  registry) and such tests skip themselves.

### Fixed

- `bench/agent_loop.py`: the receipt server's stderr log is opened under the traces directory,
  which did not exist for a fresh `AR_BENCH_RESULTS`, so every episode of a smoke run crashed
  before its first step.

## [0.2.0] - 2026-09-03

First public release. The library is a deterministic per-action effect oracle for browser
agents, shipped as an MCP server: every action returns a receipt
(dispatch → settlement → delta → verdict + recovery hint) computed from the live page, with no
model in the loop. This version is the state reached after the benchmark suite
(`bench/REPORT.md`) fed back into the library; the per-finding record is in
`docs/CHANGELOG-bench-fixes.md`.

### Added

- **Timers as a settlement signal.** `setTimeout` / `setInterval` / `clearTimeout` /
  `clearInterval` / `requestAnimationFrame` are hooked; a timer the action scheduled (delay ≤
  `timer_wait_ms`, default 5 s, `AR_TIMER_WAIT_MS`) keeps settlement busy until it fires or is
  cleared. Longer timers are listed in `signals.timers.long` and in the evidence as
  `long_timers_not_awaited`; intervals the action started are counted. `settled_by` gains
  `timers`. A 250 ms `location.assign` is now `navigated`, not `no_op`.
- **Pre-settle step** before the before-capture: a page still rendering after `load`, or a
  ticker that has not yet shown its cadence, is given up to `pre_settle_ms` (800) to go quiet
  or to be recognised as background. Recorded in `dispatch.pre_settle` and
  `timing_ms.pre_settle`. Cost is zero once a page has been seen quiet.
- **Pre-scroll before the before-capture** for element-targeting actions: an off-viewport target
  is centred *before* the first capture, so the dispatcher's scroll-into-view never enters the
  delta while a real repaint on the target (a canvas paint below the fold) still does. Recorded
  as `dispatch.preflight.prescroll` and the evidence `scrolled_into_view_before_capture`.
- **Screenshot masking of background regions**: the viewport boxes of nodes already mutating
  before the action are excluded from the pixel diff (`delta.screenshot_masked_regions`), so a
  rAF-animated element cannot read as the action's visual effect.
- `AR_TIMER_WAIT_MS` and `AR_PRE_SETTLE_MS` server options.
- Fixtures `tests/fixtures/{timers,bg,twostage,canvas,press}.html` and the tests that pin each
  finding (`tests/test_bench_fixes.py`).

### Changed

- **Animations are anchored to the dispatch.** An animation already running at dispatch is
  background and ignored, finite or perpetual; one the action started is waited on until it ends
  or is removed, even a perpetual spinner. A transient spinner between a fetch and its render is
  no longer invisible to settlement (bench F8: 100/160 stale → 0).
- **Perceptual floor for screenshot-only changes.** A screenshot delta below dHash distance 1
  and 0.5 % of pixels (a button's `:active` repaint, a focus ring) is reported as
  `screenshot_changed_weak` and no longer makes a `changed` on its own (bench F9).
- The strict receipt schema gained `settled_by: "timers"`, `signals.animations.background`,
  `signals.timers {...}`, `signals.timer_wait_ms`, `delta.screenshot_masked_regions`,
  `dispatch.pre_settle`, `timing_ms.pre_settle` and `timing_ms.prescroll`, with the invariant
  that nothing in `timers.long` is ≤ `timer_wait_ms`.

### Fixed

- A 50 ms ticker or a rAF loop no longer turns an unrelated click right after `load` into
  `changed` / `never_settled` (bench F5; accuracy cases k03, k07).
- `setTimeout`-scheduled effects (bench F4; accuracy c39, s07; settlement `timeout` mode
  116/160 stale → 0).

### Benchmarks (this version, `bench/REPORT.md`)

- Verdict accuracy 237/237 labelled trials on the fixture corpus; settlement 0 % stale across
  four landing mechanisms and eight delays; measured per-action overhead recorded per stage.

## [0.1.0] - 2026-09-02

Prototype: `ReceiptSession` (dispatch → settlement → delta → verdict), the MCP server with
`open / navigate / click / hover / select / type / press / scroll / snapshot / tabs /
receipt_last / receipt_schema / receipt_begin / receipt_end`, wrap mode over CDP, the strict
pydantic receipt schema, and 74 hermetic tests against local fixture pages.

[Unreleased]: https://github.com/JeremiahM37/action-receipt/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/JeremiahM37/action-receipt/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/JeremiahM37/action-receipt/releases/tag/v0.2.0
[0.1.0]: https://github.com/JeremiahM37/action-receipt/commit/fd4c3d8
