# Library findings from the benchmark suite

Bugs and behaviours in `action_receipt/` observed while building `bench/`. The bench does not
edit the library; each item has a repro and the expected/actual verdict. The library was being
hardened concurrently, so every item names the source state it was observed on (`lib_hash` =
SHA-1 over `action_receipt/*.py`, recorded in every `bench/results/*.json` env block and per
episode in `agent_loop_episodes.jsonl`). Items marked **[fixed upstream during the run]** were no
longer reproducible on a later `lib_hash`; the final re-run in `REPORT.md` states which hash it measured.

## F1 — Playwright's scroll-into-view is counted as an effect of a click

- **Observed on** the library state at 14:00 UTC (first `bench.accuracy` run, before `scrolled_into_view_only` existed in `verdict.py`).
- **Repro**: `bench/fixtures/acc/controls.html` in a 1000×700 viewport; `session.click("#submit-silent")`
  (a submit button whose form's `onsubmit` returns false — nothing happens). The button is below
  the fold, so Playwright scrolls it into view before clicking.
- **Expected**: `no_op` (`focus_moved_only`).
- **Actual**: `changed`, evidence `window_scrolled: dx=0 dy=622; focus_changed: …; screenshot_changed: 0.081`.
  Same for `#js-void` (dy=15), `#hash-pd`, `#submit-required`, `#hover-huge`, and `#covered`
  (which became `changed` instead of `blocked`: `dispatch_error_but_state_changed … window_scrolled dy=238`).
- **Why it matters**: below-the-fold targets are the norm on real pages, so every no-op click on
  them reads as a success — the exact failure the receipt exists to remove. The scroll is the
  dispatcher's actionability step, not the page's response, i.e. the same category as the focus
  ring that the design already excludes.
- **Status**: `verdict.py` now carries `scrolled_into_view_only` **[fixed upstream during the run]** —
  confirmed on `lib_hash 8388f1e2fc46`: all five cases are `no_op` 3/3, and `bench.accuracy`
  (targets below the fold) and `--prescroll` (targets pre-scrolled) now agree within 3 verdicts
  (93.7% vs 94.9%). The over-correction it introduced is F7.

## F2 — `dispatch_error` kept only the first line of the Playwright error, losing the block reason

- **Observed on** the same state as F1.
- **Repro**: `session.click("#offscreen")` (element at `left:-9999px`) and `session.click("#pe-none")`
  (`pointer-events:none`). Playwright's message is `Timeout 3000ms exceeded.\nCall log:\n  - … element is outside of the viewport` /
  `… <section> intercepts pointer events`.
- **Expected**: `blocked` (`_BLOCK_MARKERS` contains both phrases).
- **Actual**: `unknown`, evidence `dispatch_error: TimeoutError: Locator.click: Timeout 3000ms exceeded.` — `session.py`
  built the string from `str(e).splitlines()[0]`, so the marker never reached `decide()`.
  Also cost the full 3 s actionability timeout because pre-flight did not flag either case
  (`visible: true` for the offscreen element; `coveredBy` is null because `elementFromPoint` at
  a negative x returns null; `pointerEvents` is captured but not used).
- **Status**: `session.py` now uses `_describe_error(e)` **[fixed upstream during the run]** — confirmed on
  `lib_hash 8388f1e2fc46`: `blocked` precision and recall are both 1.00 (24/24), including `#offscreen` and `#pe-none`.

## F3 — same-document `replaceState` on an identical URL reads as `navigated`

- **Observed on** the F1 state and still on `lib_hash c8da4e718e4b` (14:05 UTC). **[fixed upstream during the run]**:
  on `lib_hash 8388f1e2fc46` `s02` is `no_op` 3/3 and `s03` is `changed` 3/3 (the evidence now distinguishes
  `cross-document` navigations).
- **Repro**: `bench/fixtures/acc/spa.html`, `session.click("#replace-same-identical")` — the handler
  calls `history.replaceState({}, '', location.href)` and re-renders identical content.
- **Expected**: `no_op` (identical URL, identical DOM).
- **Actual**: `navigated`, evidence `navigation_events: 1`. Playwright emits `framenavigated` for
  same-document history changes; `decide()` treats any main-frame navigation event that is
  not fragment-only as a navigation, without checking whether the URL actually changed.
  `#replace-same-swap` (identical URL, content swapped) is likewise `navigated` instead of `changed`.
- **Suggested fix**: only count `navigations` when the URL differs (or the event is a
  cross-document navigation); same-document history events are DOM changes at most.

## F4 — a navigation scheduled by a short timer is missed (documented limitation, measured)

- **Repro**: `spa.html` `#assign-late` → `setTimeout(() => location.assign(...), 250)`.
- **Expected**: `navigated`. **Actual**: `no_op` (`quiet_window/89ms`). Same mechanism as the
  documented `setTimeout` limitation; listed so the number is on record: `bench.settlement`
  `timeout` mode gives the exact stale-vs-delay curve for the 100 ms and 500 ms quiet windows.

## F5 — pages with permanent background activity

- `?bg=fast` (50 ms ticker) and `?bg=raf` (rAF inline-style loop): an unrelated click reads as
  `changed` (the ticker's own mutation is in the delta) with `never_settled` evidence, on every
  library state measured (still 3/3 misses each on `8388f1e2fc46`). `?bg=spinner` (perpetual CSS
  animation) read `unknown` on the first state and is `no_op` 3/3 on the final one. All are documented
  in `docs/DESIGN.md` §9; the bench records the two remaining ones as misses against ground truth
  `no_op` because an agent consuming the verdict field alone would be misled — the `never_settled`
  evidence string is the only tell. The 1 s clock (`?bg=clock`) is handled correctly on every state.

## F7 — the F1 fix masks a real screenshot-only change when the dispatcher also scrolled

- **Observed on** `lib_hash 8388f1e2fc46` (final re-run), `bench.accuracy` case `c40`.
- **Repro**: `controls.html`, 1000×700 viewport, `session.click("#canvas")` — the canvas is below the
  fold; the click handler paints a 300×120 red rectangle (no DOM change, screenshot only).
- **Expected**: `changed` (`screenshot_changed`). With `--prescroll` (target already in view) it *is*
  `changed` 3/3.
- **Actual**: `no_op`, evidence `scrolled_into_view_only: dx=0 dy=487 …`. The new rule discards the
  screenshot signal whenever the window scrolled during dispatch, because the scroll itself changes
  the screenshot. The scroll and the paint are conflated.
- **Suggested fix**: after a dispatcher scroll, compare the after-screenshot against a fresh
  post-scroll pre-action capture (i.e. re-capture `before` once the target is in view, before
  clicking), or restrict the discard to the case where the screenshot diff is explained by the
  scroll delta. `bench.accuracy` vs `--prescroll` is the regression check.

## F8 — ignoring infinite animations makes a transient loading spinner invisible to settlement

- **Observed on** `lib_hash 8388f1e2fc46` (final `bench.settlement` re-run); **not** present on the first-pass state.
- **Repro**: `bench/fixtures/settle.html?mode=twostage&delay=250` — click → `fetch('/slow?ms=250')` →
  on response insert `<span class="spinner">` (`animation: spin .6s linear infinite`) → 125 ms later remove it and
  write the result. `session.click("#go")`.
- **Expected**: settlement stays busy while the spinner runs (it was started by the action), so the after-capture
  holds `FINAL:…`. First pass: **0/160** stale across delays 0-3000 ms, `settled_by=animations|dom`, wait ≈ 1.5 × delay.
- **Actual**: `changed` with `settled_by=dom` after ≈ delay + 110 ms; the after-capture holds the spinner, not the result.
  **100/160** stale (every delay ≥ 250 ms; at ≤ 100 ms the spinner's insertion mutation still restarts the quiet window
  long enough). `receipt_q500` is stale at 2000 and 3000 ms (40/160).
- **Why**: `settle.py` now counts only *finite* running animations as busy and reports perpetual ones in
  `animations.infinite`, so the always-on spinner page (`acc/clock.html?bg=spinner`, F5) settles — but a spinner
  that appears *because of* the action is the page saying "still working", and it is dropped by the same rule.
- **Suggested fix**: anchor animations the way network requests are anchored — an infinite animation that
  started after dispatch (or on a node added after dispatch) is busy; one that was already running before
  dispatch is background. `bench.settlement` (`twostage` row) and `bench.accuracy` (`k05`) are the two sides of
  the regression check.

## Bench-side notes (not library bugs)

- The `mcp` 2.1 `MCPServer` returns tool dicts as `structuredContent`; `bench/agent_loop.py`
  handles both that and the text-content fallback.
- `receipt_on` shows the model a *summary* of the receipt (verdict, evidence[:6], hint,
  settlement line, plus url_after / new_tabs / modals / dialogs / value_after when set), not the
  full `to_dict()` — the full receipt is ~2 kB per action and would dominate the context.

## F6 — no way to change a `<select>` except by keyboard (MCP surface gap)

- **Observed in** `bench.agent_loop` (`settings_confirm`, first task definition, both arms, run 1).
- **Repro**: a task that requires choosing an `<option>`. The MCP server exposes `click`/`type`/`press`/`scroll`
  but no `select_option`. `click("#lang")` on a `<select>` in headless Chromium yields `no_op`
  (first click) then `changed` with only `screenshot_changed: 0.012` (the popup paints), and
  `click("#lang option:nth-of-type(2)")` is `blocked` (`target_not_visible`). The agent looped to
  the step cap in both arms. `press("ArrowDown", selector="#lang")` does work (accuracy case c12).
- **Why it matters**: the receipt correctly says nothing useful happened, but no hint points at
  the keyboard path, and a raw-tool agent has no path at all. Suggest a `select(selector, value)`
  tool (Playwright `select_option`) — a receipt for it falls out of the existing `value_changed` delta.
- **Bench action**: the task was redefined to use a text field so a tooling gap does not read as
  a false-completion result; the original episodes are kept in `agent_loop_traces/` only if the
  re-run did not overwrite them.

## F9 — a screenshot delta at the noise threshold turns a handler-less `press` into `changed`

- **Observed on** `lib_hash` of the bench 4 v2 pilot (2026-09-02, `bench/results/agent_loop_v2_traces/profile_save_phone__receipt_enforced__qwen3.5-4b__s20260902__r1.json`,
  turns 3, 6 and 11) and already visible in v1 (`settings_strict__receipt_on__run1.json`, step 4).
- **Repro**: `bench/fixtures/apps/profile.html?strict=1` (or `settings.html?strict=1`): `click("#save")` (silently rejected, `no_op`,
  focus now on the button), then `press("Enter", selector="#save")`.
- **Expected**: `no_op` — the handler returns early; nothing on the page changes.
- **Actual**: `changed`, evidence `screenshot_changed: 0.002 of pixels, dhash distance 0` and nothing else. `SCREENSHOT_CHANGED_THRESHOLD`
  is `0.002` (`delta.py`, commented "noise is < 0.001"), the button's active-state paint on Enter lands exactly on it, and
  `substantive_change()` accepts any screenshot change when focus did not move (`press` is in `_FOCUS_IS_EFFECT`, and here focus
  was already on the button so it did not move). `press("Enter", selector=null)` does the same (turn 6).
- **Why it matters**: this is the one verdict a DONE gate keys on. In the pilot the agent's last action before `done` was such a
  `press`, so the `receipt_enforced` gate saw `changed` and accepted a false completion that the two preceding `no_op` clicks
  had correctly flagged.
- **Suggested fix**: a screenshot-only change needs a perceptual signal (dhash distance ≥ 1) or a fraction clear of the noise
  floor (≥ 0.005–0.01) to count as substantive; a button's `:active`/focus-ring repaint is the same category as the focus move
  the design already excludes.
- **Bench workaround** (`bench/agent_loop_v2.py::effective_verdict`): the policy treats a `changed` whose only evidence is a
  screenshot delta < 1% with dhash distance 0 as `weak_visual` (non-effective). The receipt shown to the model is unchanged;
  the count of such actions is reported per arm.
- **Status**: **[fixed upstream during the run]** — on `lib_hash 1c1194827a52` (the `.lib-fixed` state the final run measured)
  `press("Enter", selector="#save")`, `press("Enter")` and a repeated `click("#save")` are all `no_op` (`no_observable_change`).
  The bench workaround stays in place as a guard and its per-arm count (`weak_visual`) is reported in `results/agent_loop_v2.md`.

## F10 — the browser's native validation bubble is a screenshot-only `changed` with no hint

- **Observed on** `lib_hash 1c1194827a52` (final bench 4 v2 run), task `profile_native` in every arm and both models —
  the only actions the bench's `weak_visual` guard (F9) fired on after the F9 fix landed.
- **Repro**: `bench/fixtures/apps/profile.html?native=1` (a real `<form>` with `<input required>`): `click("#save")`
  or `press("Enter", selector="#save")` with the required field empty.
- **Expected**: something that tells the agent the submit was intercepted — `no_op`/`blocked` with a hint naming the
  invalid field, or at least `changed` with a hint ("a validation message may be showing; the form did not submit").
- **Actual**: `changed`, evidence `screenshot_changed: 0.007 of pixels, dhash distance 0` and nothing else, `hint: null`.
  The bubble Chromium paints for `reportValidity()` is not in the DOM, so the DOM/text/value deltas are empty and the
  screenshot is the only signal; the verdict is *true* (something did appear) but it is indistinguishable from any
  other cosmetic repaint, and a DONE gate keyed on `changed` accepts it.
- **Why it matters**: HTML5 validation is the most common way a submit is intercepted on real sites; it is exactly the
  "client-side validation intercepts submit" trap. `document.activeElement` moves to the first invalid control and
  `form.checkValidity()` is false — both are observable from the page and would give a precise hint.
- **Suggested fix**: after a `click`/`press` whose target is a submit control (or inside a form), read
  `form.checkValidity()` / `:invalid` on the form; if false, report `no_op` with hint "the form's native validation
  blocked the submit: <field> is invalid/required" (or keep `changed` but attach that hint and mark the screenshot as
  `validation_bubble`).
- **Bench handling**: the `receipt_enforced` gate treats these as `weak_visual` (non-effective), so a DONE right after
  one is refused; `receipt_on` shows the raw `changed` and is therefore *misled* by it — the per-task table for
  `profile_native` is the place to see the difference.
