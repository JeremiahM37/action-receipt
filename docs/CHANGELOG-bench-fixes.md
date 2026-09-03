# Library changes from the benchmark findings

Each entry: the finding in `bench/FINDINGS.md` (or the miss in `bench/results/accuracy.md` /
`settlement.md`) → what changed in `action_receipt/` → the test that pins it. The bench was not
edited; the numbers quoted as "before" are from the bench's own result files on `lib_hash
8388f1e2fc46`, and the "after" verdicts were reproduced against the bench fixtures (read-only)
with the same viewport and quiet window before the tests were written.

## F8 — a transient spinner the action started was invisible to settlement (regression)

- **Before**: `settle.py` ignored every `iterations: Infinity` animation so the perpetual-spinner
  page could settle; the two-stage fixture (fetch → spinner → render) went from 0/160 to
  **100/160 stale** — the after-capture held the spinner, not the result.
- **Change**: animations are **anchored to the dispatch**. `MARK_JS` (evaluated in every
  same-origin frame right before the action) records the set of animations already running;
  `STATUS_JS` counts a running animation as *background* if it is in that set and as *busy* if
  not — finite or perpetual. A spinner the action put up therefore keeps settlement busy until it
  is removed (or the hard timeout); a spinner that was already spinning is set aside
  (`animations.background`). A document with no anchor (after a cross-document navigation) keeps
  the old rule: finite busy, perpetual ignored. `animations.infinite` is now the peak count seen.
- **After**: twostage 250 / 1000 / 2000 ms → `changed` with `FINAL:<delay>` in the after-capture,
  `settled_by dom`, settle ≈ 1.5–1.6 × delay; `clock.html?bg=spinner` (k05/k06) still `no_op` /
  `changed` in < 120 ms.
- **Tests**: `test_bench_fixes.py::test_f8_spinner_started_by_the_action_is_waited_on[250|1000|2000]`,
  `test_f8_perpetual_spinner_already_running_is_background_and_settles`; the existing
  `test_perpetual_css_animation_does_not_block_settlement` still passes unchanged.
  Fixture: `tests/fixtures/twostage.html` (same shape as `bench/fixtures/settle.html?mode=twostage`).

## F7 — the F1 fix discarded a real screenshot-only change below the fold

- **Before**: `scrolled_into_view_only` dropped the screenshot signal whenever the dispatcher had
  scrolled, so the canvas paint (c40) read `no_op`.
- **Change**: **re-baselining.** For element-targeting actions (`click`, `hover`, `type`,
  `select`, `press` with a selector) the target is scrolled into view *before* the before-capture
  (`PRESCROLL_JS`: instant, centred, only if some part of it or of an ancestor frame is outside
  its viewport). Playwright's own scroll-into-view then has nothing to do, so the scroll never
  enters the delta; the movement is recorded as `dispatch.preflight.prescroll {dx, dy}` and stated
  as evidence `scrolled_into_view_before_capture`. The `scrolled_into_view_only` rule is kept for
  the cases where the dispatcher still scrolls (a covered target's retries, a failed pre-scroll).
- **After**: c40 `changed` (`screenshot_changed: 0.055`, `scroll_dy: 0`); the F1 cases stay
  `no_op` (`#hover-huge`'s colour shift is below the pixel threshold, as in the bench's
  `--prescroll` run). `bench.accuracy` and `--prescroll` should now agree on every case.
- **Tests**: `test_f7_canvas_paint_below_the_fold_is_changed` (fixture `tests/fixtures/canvas.html`);
  `test_cases.py::test_click_target_below_fold_scrolls_into_view_and_is_changed` was rewritten to
  assert the new invariant (scroll in the preflight, absent from the delta) instead of the old
  mechanism (`inViewport: false`, `scroll_dy > 0`); `test_click_target_under_sticky_header_is_blocked_naming_the_header`
  still asserts `scrolled_into_view_only` for the retry case, unchanged.

## F5 / accuracy k03, k07 — a 50 ms ticker and a rAF loop read `changed` for an unrelated click

- **Cause** (reproduced): the background detector needs ≥ 3 mutation batches over ≥ 200 ms
  *after `load`*, and the bench — like an agent that acts on a freshly opened page — clicked
  within ~60 ms of `load`. The activity had not shown itself; settlement then ran to the hard
  timeout on `dom` (`never_settled`). Clicking the same pages at load + 300 ms was already
  `no_op` on the old library.
- **Change**: a **pre-settle step** before the before-capture (`settle.pre_settle`,
  `PRESETTLE_JS`): the page counts as quiet only once `quiet_ms` has been *observed* since the
  later of the last foreground mutation and `load`; until then it polls (re-marking background
  every poll) up to `pre_settle_ms` (800). A ticker or rAF loop qualifies as background after
  ~200–300 ms and the capture is taken with it set aside. Recorded in `dispatch.pre_settle` and
  `timing_ms.pre_settle`. Two supporting changes: the **timer hook** makes a `setTimeout`-chained
  ticker background (its re-arms inherit the flag), and the **screenshot diff masks the viewport
  boxes of background roots** (`screenshot_masked_regions`), so a rAF-moved element cannot read
  as the action's visual effect even when the click target takes no focus.
- **Cost**: 0 once the page has been seen quiet (one evaluate, 0.4 ms p50); ≤ 100 ms for the
  first action within 100 ms of `load`; ~300 ms for the first action on a 50 ms ticker / rAF page.
- **After**: k03, k07 `no_op` with `background_mutations_excluded` (settle 65–90 ms); k05 (CSS) `no_op`
  with `animations.background: 1`; `#counter` on each page `changed` naming only `span#count`.
- **Tests**: `test_permanent_background_activity_is_no_op_for_an_unrelated_click_right_after_load[fast|raf|css]`,
  `test_raf_loop_click_on_non_focusable_text_is_no_op_via_screenshot_mask`,
  `test_pre_settle_cost_is_bounded_by_the_quiet_window_and_zero_once_the_page_has_been_seen_quiet`.
  Fixture: `tests/fixtures/bg.html` (the `fast` mode uses a self-re-arming `setTimeout`, the
  common ticker shape, deliberately different from `clock.html`'s `setInterval`).

## F4 / accuracy s07, c39 / settlement `timeout` mode — timers scheduled by the action were missed

- **Before**: the documented `setTimeout` blind spot; 116/160 stale in the bench's `timeout` mode
  at quiet 100, `#assign-late` (250 ms `location.assign`) `no_op`, `#delayed-300` `no_op`.
- **Change**: `INSTALL_JS` hooks `setTimeout` / `setInterval` / `clearTimeout` / `clearInterval` /
  `requestAnimationFrame`. Every timer inherits a *background* flag from the callback that created
  it; the dispatch mark sets the anchor and flags all pending timers background. A pending
  `setTimeout` created after the anchor by foreground code with delay ≤ `timer_wait_ms` (5000,
  `AR_TIMER_WAIT_MS`) is a new settlement signal **`timers`** (busy until it fires or is cleared);
  longer ones are listed in `timers.long` and in the evidence as `long_timers_not_awaited`;
  intervals the action started are counted in `timers.intervals_started`. On a document without
  an anchor (after a navigation) timers are not attributed and not awaited. `settled_by` gains
  `timers` (priority after `network`).
- **Cost**: negligible (the hook is a Map insert/delete; `STATUS_JS` reads ≤ 32 pending entries).
  Measured click overhead on the fixture page after all four changes: total p50 184–195 ms vs the
  bench's 179 ms (measured while the 5× suite ran concurrently).
- **Trade-offs** (documented in `DESIGN.md` §9): a `setTimeout(hideToast, 3000)` costs a 3 s wait;
  a chain of short timers that never ends runs to the hard timeout like a click-started ticker;
  a promise continuation off a background callback is foreground.
- **After**: s07 `navigated` (`settled_by timers`, 259 ms); c39 `changed` (404 ms); `late.html`
  150 / 400 / 1500 ms all `changed` at quiet 100; settlement `timeout` mode 250 / 1000 ms landed.
- **Tests**: `test_timer_scheduled_by_the_action_is_awaited_and_its_effect_caught[150|400|1500]`,
  `test_navigation_scheduled_by_a_250ms_timer_is_navigated`,
  `test_timer_longer_than_timer_wait_is_reported_not_awaited`,
  `test_cleared_timer_releases_settlement_and_chained_timers_are_followed`,
  `test_timers_signal_can_be_disabled_to_expose_the_quiet_window_bound`. The two existing
  quiet-window tests (`test_receipt.py::test_delayed_dom_change_shows_the_quiet_window_tradeoff`,
  `test_cases.py::test_late_timer_effect_receipt_is_consistent_with_its_own_observed_window`) keep
  every old assertion under `timer_wait_ms=0` and add the default-config claim on top.
  Fixture: `tests/fixtures/timers.html`.

## F9 — a screenshot delta at the noise floor turned a handler-less `press` into `changed`

- **Before**: `press("Enter", selector="#save")` on a focused button whose handler returns early
  read `changed` with the single evidence `screenshot_changed: 0.002 of pixels, dhash distance 0`
  — the button's `:active` repaint lands exactly on `SCREENSHOT_CHANGED_THRESHOLD`, and
  `substantive_change()` accepted any screenshot change when focus had not moved. In the agent
  loop pilot this was the last action before a false `done`.
- **Change**: a screenshot-only change is substantive only with a perceptual signal
  (`verdict._visual_is_perceptual`: dHash distance ≥ 1 **or** fraction ≥ 0.005). Below that it is
  still reported, as `screenshot_changed_weak: … (below the perceptual threshold …; not counted as
  an effect)`, and the verdict falls through to `no_op`. The detection threshold (0.002) and the
  `screenshot_changed` delta field are unchanged, so nothing is hidden.
- **After**: `apps/profile.html?strict=1` and `apps/settings.html?strict=1`: click `#save` → `no_op`,
  then Enter with and without a selector → `no_op`; a real repaint (a 300×120 canvas fill on Enter,
  0.05 of pixels, dhash 4) is still `changed`.
- **Tests**: `test_f9_press_enter_on_focused_handlerless_button_is_no_op_not_changed`,
  `test_f9_real_screenshot_only_repaint_on_press_is_still_changed`. Fixture: `tests/fixtures/press.html`.

## Schema

`action_receipt/schema.py` (strict, `extra="forbid"`) gained: `settled_by: "timers"`;
`signals.animations.background`; `signals.timers {reason, busy_until_ms, peak_pending,
max_delay_ms, long (≤5), background, intervals_started}` with the invariant that nothing in
`long` is ≤ `timer_wait_ms`; `signals.timer_wait_ms`; `delta.screenshot_masked_regions`;
`dispatch.pre_settle {elapsed_ms, polls, quiet, waited_for, background_roots, max_ms}`;
`timing_ms.pre_settle` / `timing_ms.prescroll`. Every receipt produced by the suite is validated
at fixture teardown, as before.

## Docs

`docs/DESIGN.md` §1 (two new stages), §2 (anchored animations, the `timers` row, the constants,
"Anchoring"), §3 (screenshot mask), §4 (evidence names), §9 (the bounds restated: timers beyond
`timer_wait_ms`, the pre-settle window, the no-anchor document). `docs/TESTING.md`: the new file,
the new rows, the quiet-window table with a third column, the pre-settle cost table.

## Not changed

- `bench/` — untouched (a sibling agent owns it); the bench fixtures were only read.
- `README.md` — not in scope; its sample receipt (`signals.animations` without `background`, no
  `timers`) and the `no_op` evidence list (line 174) are now incomplete and should be refreshed.
- The `_dispatcher_scrolled` / `scrolled_into_view_only` rule — still needed for covered targets.

## Stability

See the bottom of `docs/TESTING.md` for the 5× sequential run recorded after these changes.

## F10 — the browser's native validation bubble was a screenshot-only `changed` with no hint

- **Before**: `apps/profile.html?native=1` (a real `<form>` with `<input required>`), `click("#save")` or
  `press("Enter", selector="#save")` with the required field empty → `changed`, evidence
  `screenshot_changed: 0.007 of pixels, dhash distance 0` and nothing else, `hint: null`. The bubble
  Chromium paints for the refused submit is not in the DOM, and 0.7 % of pixels clears the F9
  perceptual floor (0.5 %), so the verdict was *true* and useless: every arm of the agent benchmark
  (`native_validation`, both models) ended in a false DONE on it. Accuracy case c41
  (`controls.html#submit-required`) read `no_op` (`focus_moved_only`) for the same reason.
- **What the browser gives us** (verified with a capture-phase probe before writing any code): every
  bubble-showing path — a submit click, an implicit Enter submission, `requestSubmit()`,
  `reportValidity()` — fires `invalid` on each rejected control; `checkValidity()` fires it too
  (no bubble); `form.submit()` and a `novalidate` form fire nothing. The event neither bubbles nor
  crosses shadow boundaries: a capture listener on `window` sees light-DOM events (and, registered
  by the init script before any page script, is first in the path, so a page's
  `stopImmediatePropagation` cannot hide it); a shadow-root form needs a listener on that root; a
  same-origin frame's form is seen by that frame's own observer. A `checkValidity()`-after-settlement
  fallback was therefore **not** needed and is not used for the verdict: the target probe records
  `form {path, noValidate, invalid}` via `matches(':invalid')` (which fires nothing) as context only.
- **Change**: `INSTALL_JS` records every `invalid` event (`{el, t, validationMessage, true ValidityState
  flags}`) in a ring cleared at the dispatch mark; `observeRoot` adds the listener to each shadow root
  (open and closed, via the `attachShadow` hook). `CAPTURE_JS` returns the rejected controls since the
  anchor across every same-origin frame (latest event per control, first-seen order, ≤ 64) and the
  delta carries them as **`validation_blocked [{path, tag, id, name, type, message, flags}]`**. In
  `verdict.py`, `substantive_change()` returns false when the submit was refused and nothing *core*
  changed — the focus move to the first invalid control, the scroll that brings it into view and the
  bubble's pixels are the refusal, restated as `focus_moved_only` / `scrolled_into_view_only` /
  `screenshot_changed_weak` with that attribution. The verdict is then **`blocked`** (dispatch
  succeeded, the page refused the effect, nothing changed — the existing definition), the evidence
  leads with `form_validation_blocked: N control(s) rejected by the browser's constraint validation:
  input#phone (valueMissing: "Please fill out this field.")`, and the hint is `form validation blocked
  the submit: input#phone (valueMissing: "…"); fill that field and retry` (`correct` for any flag
  other than `valueMissing`). When the page *did* change alongside the refusal (it rendered its own
  error UI, or `type(…, submit=True)` changed the value) the verdict stays `changed` with the same
  evidence first and a hint saying the listed changes are the page's response to the invalid input,
  not a submit's effect.
- **After**: `profile.html?native=1` click and Enter → `blocked`, `screenshot_changed_weak: 0.006 …
  (the native validation bubble…)`, hint names `input#phone`; c41 → `blocked` 3/3 (its bench label
  moves `no_op` → `blocked` — the ground truth *is* a refused effect); `#submit-ok` still
  `navigated`; a `novalidate` form and a valid submit carry `validation_blocked: []`.
- **Schema**: `delta.validation_blocked` (strict `ValidationBlockedModel`, ≤ 64); invariants:
  `validation_blocked` non-empty ⇔ a `form_validation_blocked` evidence item, and never `no_op`.
- **Tests**: `tests/test_f10_validation.py` (10 tests) — `required` click, `pattern` mismatch (flag in
  the receipt), implicit Enter in a field / Enter on the focused button without a selector /
  `type(submit=True)` (→ `changed` + refusal evidence), a valid submit (`changed` and `navigated`,
  empty `validation_blocked`) and a non-submit click on an invalid form (`no_op`), `novalidate`, a
  form in an open shadow root and in a same-origin iframe (paths carry `#shadow-root` /
  `#document`), two invalid controls both named, and the bench's exact shape (fill name → submit →
  blocked → fill phone → submit → `changed`). Fixture: `tests/fixtures/validation.html`.
