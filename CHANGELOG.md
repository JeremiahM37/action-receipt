# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Release notes on GitHub are cut from the section
for that version.

## [Unreleased]

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

[Unreleased]: https://github.com/JeremiahM37/action-receipt/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/JeremiahM37/action-receipt/releases/tag/v0.2.0
[0.1.0]: https://github.com/JeremiahM37/action-receipt/commit/fd4c3d8
