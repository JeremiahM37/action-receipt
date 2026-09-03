# Design

`action-receipt` answers one question per action, deterministically: **did the action have an
effect, what was it, and if not, why not.** It does so in four stages and returns the whole
trail, so an agent (or a human reading the trace) can see how the verdict was reached.

## 1. The four stages

```
capture(before) ── dispatch ── settle ── capture(after) ── delta ── verdict + hint
      │               │          │             │             │
   state S0        the action   measured    state S1     diff(S0,S1)   decision table
   + preflight     (playwright) quiescence  + events                    (no model)
```

| stage | what happens | cost measured on the fixtures |
|---|---|---|
| **pre-scroll** | For element-targeting actions, if the target is not fully inside its viewport it is scrolled into view (instant, centred) *before* the before-capture. Playwright would scroll it during dispatch anyway; doing it first keeps the scroll — and every pixel it moves — out of the delta, so a real screenshot-only effect on a below-the-fold target (a canvas paint) is still seen (bench F7). Recorded as `dispatch.preflight.prescroll {dx, dy}` and stated in the evidence as `scrolled_into_view_before_capture`. | 0 in view; ~2 ms when a scroll is needed |
| **pre-settle** | The before-capture is only taken once the page has been *observed* quiet for `quiet_ms` (last foreground mutation or `load`, whichever is later), re-marking background on every poll and bounded by `pre_settle_ms` (800). A page acted on within 100 ms of `load` waits out the remainder; a ticker or rAF loop that has not yet shown its cadence gets classified before the capture instead of timing out settlement (bench F5 / k03 / k07). Recorded in `dispatch.pre_settle` and `timing_ms.pre_settle`. | 0 once the page has been seen quiet; ≤ 100 ms right after `load`; ~300 ms on a page with a 50 ms ticker clicked at `load` |
| **dispatch** | The action is delivered with Playwright (click/hover/select/type/press/wheel/goto). Pre-flight ran first: if the target is disabled, covered, invisible, readonly, `pointer-events:none` or off the document, the actionability wait is shortened to 300 ms so a `blocked` receipt is cheap; a missing `<select>` option is detected without any wait. Exceptions are caught and become `dispatch.error` (first line plus any actionability reason Playwright buried in its call log), never lost. Right before dispatch the **anchor** is set in every same-origin frame: animations already running and timers already pending become background (§2). In *wrap mode* the caller dispatches with another tool and this stage is empty. | 5–20 ms |
| **settlement** | Poll a set of quiescence signals until all are quiet (§2). | 86–90 ms for a no-op; the real duration of the work otherwise |
| **delta** | Diff the two captures and the events observed in the window (§3). | ~1 ms |
| **verdict** | A fixed decision table (§4) over the delta; produces the verdict, a machine-readable evidence list and a hint. | ~0 |

Both captures cost 50–75 ms each on the fixture pages (DOM walk plus a viewport screenshot;
the walk alone is ~100 ms at 12,000 elements and 116–180 ms at the 20,000 cap, see `docs/TESTING.md`). A complete no-op receipt
is ~240 ms end to end — less than half of playwright-mcp's two blind 500 ms sleeps, and it
*knows* it was a no-op.

## 2. Settlement signals and timeouts

Settlement replaces "sleep N seconds" with "observe until every signal has been quiet". The
receipt records which signal was the last to go quiet (`settled_by`) and how long it took.

| signal | source | quiet condition | notes |
|---|---|---|---|
| `network` | Playwright `request` / `response` / `requestfinished` / `requestfailed` / `download` events | no request **started after dispatch** is still pending | `websocket`, `eventsource`, `media` are excluded (they never finish). Pre-existing long-polls are ignored — they were not caused by this action. A request that became a download is released at the `download` event. **Unconsumed bodies**: Chromium never reports a response finished while the page has not read its body, and pages routinely do `fetch(url).then(r => r.status)`; so a response with a small `Content-Length` (≤ 64 KB) is released at its headers (the body is already buffered) and any other response is released `body_grace_ms` after its headers. Releases are listed in `signals.network.released` with the reason |
| `dom` | `MutationObserver` on `document` **plus every shadow root** (open and closed — `attachShadow` is hooked by the init script) **plus every same-origin child frame** (each frame has its own observer; counters are aggregated onto the top frame's clock) | no *foreground* record for `quiet_ms` | page-clock timestamps; the observer is re-anchored to 0 after a navigation. Mutations inside **background nodes** (§3) are counted in `dom.background_mutations` and do not keep the page busy |
| `scroll` | `scroll` listener, capture phase | no event for `quiet_ms` | catches smooth-scroll and JS-driven scrolling |
| `layout` | `PerformanceObserver({type: 'layout-shift'})` | no entry (without recent input) for `quiet_ms` | |
| `animations` | `document.getAnimations()` in every same-origin frame | no `running` animation that **started after dispatch** | CSS transitions and Web Animations; **not** rAF loops. Anchored to the dispatch: an animation already running at the anchor is background (`animations.background`) whether finite or perpetual — it is not this action's work; one that started after the anchor is waited on until it ends or its element is removed, **even if perpetual** (`iterations: Infinity`), because a spinner the action put up is the page saying "still working" (bench F8: fetch → spinner → render went 100/160 stale when perpetual animations were simply ignored). A document with no anchor (after a cross-document navigation) falls back to: finite busy, perpetual ignored (`animations.infinite` counts them) |
| `timers` | `setTimeout` / `setInterval` / `requestAnimationFrame` hooked by the init script | no `setTimeout` scheduled after dispatch by *foreground* code with delay ≤ `timer_wait_ms` (5 s) is still pending | Every timer inherits *background* from the callback that created it, so a ticker re-arming itself with `setTimeout(tick, 50)` is background and never awaited; a timer the click handler scheduled is foreground and keeps settlement busy until it fires or is cleared. Longer timers are reported in `timers.long` and in the evidence (`long_timers_not_awaited`), not awaited; intervals the action started are counted in `timers.intervals_started`. On a document with no anchor timers are not attributed and not awaited. This closes the `setTimeout` blind spot for delays up to `timer_wait_ms`: `late.html` 150 / 400 / 1500 ms are now caught at quiet 100, and a `location.assign` scheduled 250 ms out is `navigated` (bench F4 / accuracy s07, c39) |
| `document` | `document.readyState`; the status evaluate itself | `complete`, and the execution context is alive | a destroyed context (mid-navigation) counts as busy |
| `navigation` | `framenavigated` on the main frame | after each new navigation, `wait_for_load_state('load')`, then keep polling | a main-frame *document request* alongside it marks a cross-document navigation; `pushState`/`replaceState`/hash changes fire `framenavigated` alone |

A page that **closes or crashes** cannot settle: settlement is *aborted* immediately
(`aborted: page_closed | page_crashed`, `settled_by` names the same) instead of spinning to the
timeout, and the verdict is `unknown` with that evidence. When several signals were busy in the
same last poll, `settled_by` is chosen by a fixed priority (navigation, network, timers,
animations, dom, layout, scroll, document) so the value is stable run to run.

Constants: `quiet_ms = 100` (the window that dom/scroll/layout must show; also the minimum
observation time after dispatch, and the quiet the pre-settle step insists on before the
before-capture), `poll_ms = 20`, `timeout_ms = 10000`, `nav_load_timeout_ms = 10000`,
`body_grace_ms = 1500`, `timer_wait_ms = 5000`, `pre_settle_ms = 800`. Environment:
`AR_QUIET_MS`, `AR_SETTLE_TIMEOUT_MS`, `AR_BODY_GRACE_MS`, `AR_TIMER_WAIT_MS`, `AR_PRE_SETTLE_MS`.

**The one constant that survives, and why it is different.** `quiet_ms` is not a guess at how
long the page needs; it is the length of quiet we insist on *seeing*. Every mutation or
scroll event restarts it, so a 1.5 s fetch followed by a DOM update is waited out
(`test_slow_fetch_settlement_waits_for_network`: settled in ~1.6 s, `settled_by=dom`, the
late node is in the delta; the 0.5 s blind sleep run alongside captures the stale state). A
timer the action scheduled is itself a signal now (`timers`), so a bare `setTimeout(fn, 350)`
with nothing else to watch is waited out too (`test_delayed_dom_change_shows_the_quiet_window_tradeoff`:
caught at quiet 100, `settled_by: timers|dom`). What the loop still cannot see is work
scheduled by a mechanism it does not hook — a timer longer than `timer_wait_ms` (listed as
`long_timers_not_awaited`), a promise chain off a background callback, a Web Worker message.
The same test runs with `timer_wait_ms=0` to show the bound: the 350 ms change is missed at
quiet 100, caught at 500, and the receipt says `settled_by: quiet_window, quiet_ms: 100,
timers.long: [350]`, which tells the reader exactly how much was observed and what was not
waited for. No finite wait can do better than state its bound.

**Anchoring.** Background is decided by *when something started*, not by what it is. The
dispatch anchor (`MARK_JS`, set in every same-origin frame right before the action) records the
animations already running and marks every pending timer as background; the pre-settle step
and the before-capture mark the elements that were already mutating. Anything that begins after
the anchor — a mutation, a request, an animation (perpetual or not), a timer created by
foreground code — is the action's work and is waited on. Anything that was already going on is
set aside and named. This is the rule that lets a perpetual spinner page settle *and* a spinner
the action put up be waited for; before it, one of the two was always wrong.

On timeout the receipt lists `busy_at_timeout` (e.g. `["network"]` with the offending URL in
`signals.network.reason`), the first evidence item is `never_settled: still busy [...] after
N ms`, and the verdict falls to `unknown` if nothing was seen to change — or stays `changed`
with a hint that the listed changes may be the background activity rather than the action's
effect. `timing_ms.observed_window` (dispatch start → after-capture end) states the window in
which an effect could have been seen, so a late effect that was missed is missed *legibly*.

## 3. Delta fields

All fields are computed from two `Capture`s and the events recorded between them. Nothing is
sampled or inferred.

| field | from | used by |
|---|---|---|
| `url_changed` / `url_only_fragment` / `title_changed` | `location.href`, `document.title` | navigated / changed |
| `scroll_dx`, `scroll_dy` | `window.scrollX/Y` | changed; scroll hint |
| `container_scroll {path, before, after, delta}` | the target element if it scrolls, else its nearest scrollable ancestor (`overflow-y: auto|scroll` and `scrollHeight > clientHeight`) | changed; scroll hint |
| `frame_scrolls [{path, before, after, delta}]` | `window.scrollX/Y` of every same-origin iframe, matched by path | changed; scroll hint names scrollable frames |
| `focus_changed`, `focus_before/after` | `document.activeElement` | changed only for `press`/wrap (see §4) |
| `dom_changed`, `nodes_added/removed/modified` (≤12 each), `nodes_changed_total`, `element_count_delta` | per-element signature: tag + a fixed attribute allowlist (`class name type href src role aria-* disabled hidden open placeholder selected readonly style data-state`) + input value (masked for passwords) + checked state + `checkVisibility()` (so a CSS-revealed menu is a DOM change, not just a screenshot one) + own text (≤200 chars); path = `tag#id` or `tag:nth-of-type` chain, `#shadow-root` / `#document` for shadow and iframe boundaries; the walk covers open **and** closed shadow roots and same-origin iframes, is O(n) (paths are built incrementally; 106 ms median for 12k elements, 116–180 ms at the 20k cap — `docs/TESTING.md`), and is capped at 20,000 elements. Past the cap `fingerprint_truncated {fingerprinted, total}` is set and stated in the evidence — the text hash and screenshot still apply | changed; the listed nodes are the receipt's "what" |
| `nodes_background` (≤12), `nodes_background_total`, `background_roots` | the pre-settle step and the before-capture ask the observer which elements mutated **repeatedly** in the last 1.5 s after `load` (≥ 3 batches over ≥ 200 ms — a ticker, a feed, a clock ≤ ~700 ms; not a one-off render or the parser). Those roots and their subtrees are *background*: their changes are split out of the node lists, `dom_changed` is false if nothing else changed, `text_changed` is not counted, their mutations do not keep settlement busy, and their viewport boxes are **masked out of the screenshot diff** (`screenshot_masked_regions`), so a rAF-animated element cannot read as the action's visual effect either | evidence `background_mutations_excluded`; the no_op hint names the roots, because an effect *inside* one would be invisible |
| `text_changed` | FNV-1a over `innerText` (hash only — the text is not shipped) | changed |
| `value_before/after/changed` | the target's `.value` | changed; type hint |
| `modals_opened/closed` | visible `dialog[open]`, `[role=dialog|alertdialog]`, `[aria-modal=true]` | changed + hint |
| `screenshot_hamming`, `screenshot_changed_fraction`, `screenshot_changed`, `screenshot_masked_regions` | 64-bit dHash of the viewport PNG; fraction of pixels moving > 24/255 at 160 px width, with the background roots' boxes (before and after) masked out; changed if ≥ 0.002 | changed (canvas/video/CSS-only state that leaves no DOM trace) — on its own only if dhash ≥ 1 or fraction ≥ 0.005 (§4) |
| `dialogs` | Playwright `dialog` event (alert/confirm/prompt/beforeunload); auto-accepted (configurable) and recorded | changed + hint; a dismissed `beforeunload` is reported as the navigation being cancelled |
| `navigations`, `cross_document_navigations`, `new_tabs` | `framenavigated` (main frame), main-frame document requests, `context.on('page')` | navigated (only cross-document ones, or a changed URL) |
| `downloads` | Playwright `download` event | changed; hint says not to retry |
| `http_errors` (≤5) | responses with status ≥ 400 started after dispatch | evidence; hint for navigations to an error page and for failed fetches |
| `validation_blocked [{path, tag, id, name, type, message, flags}]` (≤64) | the `invalid` events the browser fired since the dispatch mark — on a submit click, an implicit Enter submission, `requestSubmit()`, `reportValidity()` (and `checkValidity()`, which shows no bubble). Recorded by the init script in the capture phase on `window` (first in the event path, registered before any page script) and on every shadow root (the event is not composed), in every same-origin frame; `flags` are the `ValidityState` members that are true (`valueMissing`, `patternMismatch`, `typeMismatch`, `rangeUnderflow`, …), `message` is `validationMessage`. The target probe also carries `form {path, noValidate, invalid}` (via `matches(':invalid')`, which fires nothing) as context only — every bubble-showing case was verified to fire the event, so the verdict keys on the events alone | blocked (bench F10); the hint names the field and the browser's message |
| `console_errors` | `console.error` + `pageerror` | evidence only |
| `page_crashed`, `captures_ok` | `crash` event; both `Capture.ok` | unknown |

Why a normalized DOM signature and not the accessibility tree: the aria tree drops exactly
the things a no-op check needs (a `style` flip that shows a modal, a `disabled` attribute, a
hidden input's value). The aria tree is still exposed through `snapshot()` for element
selection; it is just not the fingerprint.

## 4. Verdict decision table

Evaluated top to bottom; first match wins. "changed" below means `substantive_change()`:
any delta field except focus-only and small focus-ring screenshot changes — for `press` and
wrap-mode receipts focus moves *do* count; a screenshot change ≥ 2 % of pixels counts
regardless. A **screenshot-only** change counts only with a perceptual signal: dHash distance
≥ 1 or ≥ 0.5 % of pixels. Below that it is reported as `screenshot_changed_weak` and not
counted — a button's `:active` repaint on Enter lands at exactly the 0.2 % detection threshold
with dhash 0 (bench F9), the same category as the focus move.

| # | condition | verdict | evidence added | hint |
|---|---|---|---|---|
| 0 | the tab closed or crashed (settlement aborted) | `unknown` | `page_closed` / `page_crashed` | open a new tab |
| 1 | a capture failed | `unknown` | `capture_failed` | re-observe |
| 2 | settlement never settled (timed out) **and** nothing changed | `unknown` | `never_settled: still busy [...] after N ms` | page never went quiet |
| 3 | pre-flight found target `disabled` / `coveredBy` / `visible: false` / `pointer-events: none` / `offscreen` / (type) not editable, the select option does not exist, **or** dispatch raised an actionability error (`intercepts pointer events`, `not visible`, `not enabled`, `not attached`, `outside of the viewport`, `not editable`, `did not find some options` — read from the whole Playwright message, not just its first line) — **and** nothing changed | `blocked` | `target_disabled: <path>`, `target_covered_by: <path> <tag> <text>`, `target_not_visible`, `target_pointer_events_none`, `target_offscreen`, `target_not_editable`, `dispatch_error: …` | specific to the cause |
| 3b | `validation_blocked` is non-empty — the browser's constraint validation refused the submit — **and** nothing changed. The focus move to the first invalid control, the scroll that brings it into view and the bubble it paints (0.4–0.7 % of pixels, dhash 0–2, no DOM trace: bench F10) are the refusal, not an effect, and are stated as `focus_moved_only` / `scrolled_into_view_only` / `screenshot_changed_weak` with that attribution | `blocked` | `form_validation_blocked: N control(s) …: input#phone (valueMissing: "Please fill out this field.")` — always first | `form validation blocked the submit: <field> (<flags>: "<message>"); fill/correct that field and retry` |
| 4 | dispatch raised something else and nothing changed | `unknown` | `dispatch_error` | re-observe |
| 5 | `url_changed` (not fragment-only) **or** `new_tabs` **or** a cross-document navigation | `navigated` | `url_changed`, `navigation_events`, `new_tab_opened`, `http_error`, `js_dialog` | which tab to switch to; HTTP error destination; beforeunload handling |
| 6 | changed | `changed` | every non-empty delta field; if a modal/dialog/download appeared, that first; if pre-flight looked blocked, `state changed although the target looked blocked`; `never_settled` first if it timed out; `form_validation_blocked` first if the browser refused a submit while the page changed (it rendered its own error UI, or a `type(…, submit=True)` changed the value) | modal/dialog/download/HTTP-error guidance; a warning when the page never settled; for a refused submit, that the listed changes are the page's response to the invalid input, not a submit's effect |
| 7 | otherwise | `no_op` | `no_observable_change` (+ `focus_moved_only` / `screenshot_changed_weak` / `scrolled_into_view_only` / `scrolled_into_view_before_capture` / `dom_mutated_without_fingerprint_change` / `history_changed_same_document` / `background_mutations_excluded` / `long_timers_not_awaited` as applicable) | per action — see below |

"changed" excludes three things that are side effects of *attempting* the action: the focus
move (except for `press` and wrap mode), the scroll Playwright makes to bring an off-viewport
target into view — or its retries against a covered target — for any action other than
`scroll`, and changes inside background nodes. Each is named in the evidence so nothing is
hidden; it just does not count. A fourth exclusion applies only when the browser refused a
submit (`validation_blocked`): then focus, scroll and screenshot are all the validation UI —
the bubble is painted by the browser outside the DOM, focus jumps to the first invalid control
and it is scrolled into view — so only a core change (DOM, value, URL, modal, dialog, download)
counts, for every action including `press`. The scroll-into-view case is normally not in the delta at all
any more: the target is scrolled into view *before* the before-capture (§1, evidence
`scrolled_into_view_before_capture`), so the exclusion rule (`scrolled_into_view_only`) is only
reached when the dispatcher still had to scroll — a covered target's retries, or a pre-scroll
that failed — and it discards the screenshot signal only in that case.

`no_op` hints are computed, not templated: for `scroll`, whether the document is scrollable
at all, whether it has `overflow:hidden`, whether the window/target is already at the
bottom/top, the target's scrollable ancestor with its `scrollTop/max`, and up to three
scrollable containers on the page with their positions — the exact information the
browser-use `scroll` action lacks when it reports `Scrolled down 1000px` on a page that did
not move. For `click`, that the click was delivered but nothing changed within the observed
window, with the observed window stated. For `type`, that the value did not change and the
likely reasons (wrapper element, readonly, input mask).

### 4b. The `done` gate

The verdict says whether *an action* had an effect; the `done` gate (`action_receipt/policy.py`,
the `done` tool with `--enforce-done`) is the one place that information is allowed to *refuse*
something. It is the benchmark's `receipt_enforced` arm as a server feature, and it is as
mechanical as the verdict: a function of the last receipt's verdict and a per-claim counter.

| enforce | last receipt | refusals so far | decision |
|---|---|---|---|
| off | any, or none | any | `accepted` — the tool is inert; the result still carries `last_receipt` |
| on | `changed` / `navigated` | any | `accepted` |
| on | none (no action performed yet) | < `max_refusals` | `refused`; reason *no action has been performed in this session*; a fixed hint |
| on | `no_op` / `blocked` / `unknown` | < `max_refusals` | `refused`; reason names the verdict; `hint` is the receipt's own hint |
| on | none / `no_op` / `blocked` / `unknown` | ≥ `max_refusals` | `accepted`, `overridden: true`; reason and hint still reported |

`refusals` counts the refusals of the current claim and resets to 0 when a `done` is accepted
(either way), so a long-lived session gets a fresh budget per task and can never be trapped:
`max_refusals` (default 2) is the most extra steps the gate can cost. A verdict the policy does not
recognise is treated as non-effective — an unknown word is not evidence of an effect. Wrap-mode
receipts count like any other. No browser is started to answer `done` or `receipt_policy`: with
no session there is no receipt, and that is the answer.

The gate's reach is exactly one receipt deep, and that is the honest bound: a false DONE *after*
a `changed` action passes it, and a refusal is spurious when the task was already complete
(`bench/results/agent_loop_v2.md` counts both; the README's *Enforcing the receipt* quotes them).
The harness policy the benchmark was first run with also treated a `changed` whose only evidence
is a screenshot delta at the noise floor as non-effective (`weak_visual`, F9); the shipped gate
keys on the verdict alone, because the perceptual floor now lives in the verdict itself (§4).

## 5. Wrap mode

`receipt_begin(label, selector?, page_url?)` captures the before-state and starts the window;
`receipt_end()` settles, captures, diffs and decides. The caller performs the action with any
other tool in between. This requires sharing the browser: run this server with `--cdp` against
the same Chromium the other tool drives (playwright-mcp, browser-use and Anthropic's loop can
all attach over CDP). The observer init script is added to the shared context, so pages that
context navigates from then on carry the mutation/scroll/layout counters; pages that were
already open get the script evaluated directly when first observed (the counters then start
at that moment, which is all wrap mode needs).

`--cdp-listen PORT` is the other way round: this server launches the Chromium with a
remote-debugging port on `127.0.0.1` and attaches to it over CDP itself, working in the default
context, so a second tool pointed at `http://127.0.0.1:PORT` (playwright-mcp `--cdp-endpoint`)
shares the tabs and wrap mode observes it. The browser is started with the server rather than on
the first tool call, because the other tool's first call may come first; `browser_info` reports
the endpoint. `docs/using-with-playwright-mcp.md` has the configuration and the protocol.

`--cdp-new-context` gives an isolated incognito context instead — safe for smoke tests against
a browser someone is using, at the cost of not seeing their tabs. The read-only attach test
against a live headed Chromium (six user tabs) ran in that mode: tabs before == tabs after,
and the incognito context was gone after `close()`.

## 6. Deliberately not done

**No LLM judge — anywhere.** The reason is measured, not aesthetic. *Let's Think in Two Steps:
Mitigating Agreement Bias in MLLMs with Self-Grounded Verification* (arXiv 2507.11662,
Jul 2025) evaluated MLLM verifiers "across web navigation, computer use, and robotics,
spanning 13+ models, 28+ designs, and thousands of trajectories" and found "a strong tendency
for MLLMs to over-validate agent behavior — a phenomenon we term agreement bias. This bias is
pervasive, resilient to test-time scaling." A verifier with a bias toward agreeing that an
action worked is the *same* failure as `Scrolled down 1000px`, moved one layer up. A receipt
must be something the agent cannot talk into agreement; a diff is.

**No task-level completion judgement.** The receipt is per action. "Is the task done" is a
different, harder question with its own literature (and its own agreement bias). Keeping the
scope per-action is what keeps the oracle deterministic and cheap enough to run on every step.
The `done` gate (§4b) is not an exception: it reads the last receipt, never the task, and says
so in its two named limits.

**No screenshots shipped to the model.** Only hashes and fractions. The agent already has
its own observation channel; the receipt is the *diff*, not a second observation.

**No self-healing / retry.** The hint says what to try; the agent decides. Automatic retry
inside the oracle would re-create the "runtime decided it worked" problem with extra steps.

## 7. Future work: the desktop / AT-SPI path

Same four stages, different substrate. Settlement signals map to: X11 damage events or
Wayland frame callbacks (the screenshot-change signal), AT-SPI `object:children-changed` /
`object:state-changed` / `object:text-changed` events (the DOM-mutation analogue), and window
manager `_NET_ACTIVE_WINDOW` / focus events. Delta: a normalized AT-SPI tree walk (measured
at 87 ms median for 234 nodes on this box, cycle-6 seam notes) plus the same dHash/changed-pixel
measure on the screen. The interesting lever is that AT-SPI exposes `Action.DoAction`, so an
action can be *invoked* rather than synthesized as pointer input — sidestepping Wayland's
missing input synthesis entirely. The desktop path is a proving ground, not the product: its
audience is small and it needs `GTK_MODULES=gail:atk-bridge` / `ACCESSIBILITY_ENABLED=1`
on every app, which is off by default on a real desktop.

## 8. The two named risks and how the design meets them

**Risk 1 — browser-use absorbs the browser half.** Real and live: `docs/PREMISE.md` §Q2
shows a community PR (#5145, open since 2026-07-06, models only, unwired) that defines
`ActionEvidenceOutcome = Literal['changed', 'no_change', 'navigation', 'failed', 'attention',
'unknown']` and a `PageFingerprintDelta` — the same idea. If a maintainer merges and wires it,
browser-use users get most of this for free. Three things keep the project standing anyway:
(a) browser-use's fix can only live inside browser-use; playwright-mcp, Anthropic's loop,
Skyvern, UI-TARS and OSWorld have the same hole and none can import it, which is why this is
an MCP server and not a PR; (b) the *wrap mode* means the receipt is additive to whatever
tool the agent already uses, including browser-use itself over CDP; (c) their fingerprint is
`url + text_hash + element_count` — it cannot see a scroll, a value, a modal, a covered
target or the settlement time, so even after a merge the receipt is a superset. Watch
`browser_use/agent/views.py` and `browser_use/tools/service.py` for that enum landing.

**Risk 2 — drifting into an LLM-auditor framework.** That is the crowded graveyard
(LongHorizon-Harness and friends: per-task, model-judged, expensive). The design rule that
prevents the drift is mechanical: *every field in the receipt must be a function of the page
state and the event log.* If a proposed field needs a model to compute, it does not go in the
receipt. Hints are computed from the same fields — string templates over measured values,
never generated text. The MCP surface is per-action by construction (`{result, receipt}` on
each tool call); there is no "evaluate this trajectory" entry point, and adding one would be
a scope change, not an extension.

## 9. Known limitations

Every one of these is exercised by a test in `docs/TESTING.md`, with the measured number.

- Effects scheduled by a `setTimeout` longer than `timer_wait_ms` (5 s), or by a mechanism the
  init script does not hook (a promise chain continuing off a background callback, a Web
  Worker message, a `MessageChannel` tick), arrive after the window with nothing keeping the
  loop alive and are missed; the receipt states `timing_ms.observed_window` and lists
  `long_timers_not_awaited` so the reader knows the bound. Timers ≤ `timer_wait_ms` scheduled by
  the action *are* awaited, including one that navigates — the trade-off is that a
  `setTimeout(hideToast, 3000)` costs a 3 s wait, and a chain of short timers that never ends (a
  poll loop the action started) runs to the hard timeout like a click-started ticker does.
  Timers on a document with no dispatch anchor (the page a navigation landed on) are not
  attributed and not awaited.
- Background attribution needs the activity to have *shown itself*: ≥ 3 mutation batches over
  ≥ 200 ms in the 1.5 s before the action, after `load`. The pre-settle step gives it the
  chance — an action within 100 ms of `load` waits out the rest of a quiet window (≤ 100 ms),
  and a page that is still mutating is watched for up to `pre_settle_ms` (800) until it is
  quiet or its activity qualifies — so a 50 ms ticker or a rAF loop is background even for the
  first click after load. A 1 s clock, or a feed that ticks for the first time right after the
  action, is still not background — the first case is usually absorbed by the quiet window, the
  second is honest `changed`. Activity that *starts* with the action (a click that starts a
  ticker, or puts up a spinner that is never taken down) can never be set aside: settlement
  hits the hard timeout and the receipt says `never_settled`. And an effect *inside* a
  background node is not reported — the hint names the roots so the agent can look there — and
  its pixels are masked out of the screenshot diff, so a background root that covers the whole
  viewport (a live container the page re-renders continuously) blinds the screenshot measure
  for everything under it.
- After a cross-document navigation the new document has no dispatch anchor: perpetual
  animations there are ignored (the pre-F8 rule) and timers are not awaited, so a landing page
  that shows a spinner and renders later is captured as `navigated` in its loading state.
- A response whose body the page never reads is released after `body_grace_ms` (1.5 s) if it
  has no small `Content-Length`; a large body that the page *will* consume later than that is
  therefore not waited for.
- Cross-origin iframes are not walked; changes inside them show up only through the
  screenshot measure (a small text change there is invisible). Closed shadow roots *are*
  walked when the page was created with the init script in place; on a page attached to
  late (wrap mode over CDP) only its open roots are found.
- The DOM signature includes `style` and `class`, so pages that animate inline styles
  continuously will read as `changed` unless the animated element qualifies as background.
  rAF-driven inline-style animation is not visible to `getAnimations()`; it is caught by the
  mutation observer and set aside as background once it has shown its cadence (its box is
  masked from the screenshot diff too).
- rAF loops, WebGL and `<canvas>` content are visible only through the screenshot measure.
  A canvas paint on a below-the-fold target is seen because the target is scrolled into view
  before the before-capture; a canvas change that happens while the dispatcher is *still*
  scrolling (a covered target's retries) is conflated with the scroll and discarded.
- Beyond 20,000 elements the fingerprint is truncated (`fingerprint_truncated` in the delta
  and evidence); a change past the cap is seen by the text hash but cannot be named.
- Popup blocking cannot be reproduced through Playwright's normal launch (it passes
  `--disable-popup-blocking`); the receipt for a blocked `window.open` was verified with
  Chromium's `--block-new-web-contents`, which blocks at the browser level.
- Wrap mode's before-capture is taken when `receipt_begin` is called, so anything the other
  tool did *between* its own navigation and the begin call is not in the window.
