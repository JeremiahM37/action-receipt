# Testing

`pytest -q` runs 93 hermetic tests in ~115 s. Everything is served from `tests/fixtures/` by a
local HTTP server started in `conftest.py`; nothing reaches the network. Every receipt any
test produces is validated against `action_receipt/schema.py` when the test's session is torn
down (`validate_all`), so a schema regression fails the test that caused it.

**Stability**: the whole suite was run 10× in a row (`for i in $(seq 10)`) on 2026-09-02, and
5× again after the benchmark fixes (`docs/CHANGELOG-bench-fixes.md`); both results are at the
bottom of this file. Zero flakes is the bar; a rerun that goes green is a race
to find, not a pass.

## Files

| file | what |
|---|---|
| `tests/integration/test_receipt.py` | the original 21 cases: scroll no-op/at-bottom/inner container, link + fragment + new-tab navigation, disabled / covered / readonly targets, typing (+ password masking), slow fetch vs a blind sleep, the quiet-window trade-off, modal, alert, wrap mode, serialisability |
| `tests/integration/test_cases.py` | 41 cases: the awkward pages below |
| `tests/integration/test_robustness.py` | CDP attach to a Chromium the test launches, wrap mode around an independent CDP client, `--cdp-new-context` isolation, two pages in one browser, closed tab, crashed tab |
| `tests/integration/test_bench_fixes.py` | 19 cases pinning the benchmark findings (`bench/FINDINGS.md`, `docs/CHANGELOG-bench-fixes.md`): the transient spinner the action started (F8) at 250 / 1000 / 2000 ms, the perpetual spinner still settling, the below-the-fold canvas paint (F7), a 50 ms `setTimeout` ticker / rAF loop / CSS-only animation clicked *right after load* (F5, k03, k07), the rAF page clicked on non-focusable text (screenshot mask), timers the action scheduled at 150 / 400 / 1500 ms now caught, a `location.assign` 250 ms out (F4, s07), a 6 s timer reported-not-awaited, a cleared timer, chained timers, the timers signal switched off to expose the old bound, the pre-settle / pre-scroll cost, and Enter on a focused handler-less button vs a real canvas repaint (F9) |
| `tests/integration/test_f10_validation.py` | 10 cases pinning bench F10 (`fixtures/validation.html`): a submit the browser's constraint validation refuses is `blocked` with `form_validation_blocked` naming the field, its `ValidityState` flag and the browser's message — `required` click, `pattern` mismatch, implicit Enter in a field / Enter on the focused button without a selector / `type(submit=True)` (`changed` + the refusal evidence), a valid submit (`changed` and `navigated`, `validation_blocked: []`), a non-submit click on an invalid form (`no_op`), `novalidate`, a form in an open shadow root and in a same-origin iframe, two invalid controls, and the bench's exact `profile_native` shape through to the successful save |
| `tests/integration/test_schema.py` | one real receipt per verdict validates; the invariants reject 15 kinds of drift; the JSON-Schema export |
| `tests/e2e/test_mcp.py` | the real server over stdio with the `mcp` client: list tools, `open` → `scroll` → `navigate` → `click` (changed / blocked / no_op), `receipt_last`, `snapshot`, `tabs`, `receipt_schema`, wrap mode; and that failures are structured results, not protocol errors |

## Cases in `test_cases.py`, and what each one proved or fixed

| case | fixture | receipt | note |
|---|---|---|---|
| SPA `pushState` (no load event) | `spa.html#push` | `navigated`, `url_changed`, `navigation_events: 1 (cross-document: 0)`, the re-rendered node listed, settles < 2 s | Playwright fires `framenavigated` for same-document navigations |
| `replaceState` to a new URL, nothing rendered | `#replace` | `navigated`, `dom_changed: false` | |
| `replaceState` on the **same** URL, identical content | `#replace-same` | `no_op`, `history_changed_same_document` | **bug fixed** (bench F3): any `framenavigated` used to read as `navigated`. Now a navigation needs a main-frame *document request* or a changed URL |
| `replaceState` same URL, content swapped | `#replace-same-swap` | `changed`, not `navigated` | |
| hash-only change, no anchor target | `#hash` | `changed`, `url_fragment_changed`, `scroll_dy: 0` | |
| same-URL identical re-render | `#rerender` | `no_op` + `dom_mutated_without_fingerprint_change: N` and a hint that the handler ran | mutations are now counted *since dispatch* (the old count was cumulative since install) |
| `location.reload()` | `#reload` | `navigated`, `navigation_events` with cross-document 1, URL unchanged | |
| click inside a same-origin iframe | `frame-outer.html` → `#f` | `changed`; target path `iframe#f>#document>…>button#inner-btn`; the frame's mutation is aggregated (`frames_observed >= 2`) | **new**: `frame=` parameter, iframe walk, per-frame observer aggregation on the top clock, target probe in the frame's own context |
| wheel inside the iframe | | `changed`, `frame_scrolled: iframe#f 0 -> N`, window `scroll_dy: 0`, `dom.mutations: 0` | **bug fixed**: since-dispatch mutation count subtracted only the top frame while the status aggregated all frames (48 phantom mutations) |
| wheel over the page at the viewport centre (lands on the iframe) | | `changed` (frame or window scrolled) | |
| cross-origin iframe (`localhost` vs `127.0.0.1`) | `#x` | frame reported `crossOrigin: true`; a large visual change is `changed` via `screenshot_changed` only, `dom_changed: false` | documented limit: not fingerprinted |
| open shadow root | `shadow.html` `#open-host #inc` | `changed`, node `x-counter#open-host>#shadow-root>span#n`, `settled_by: dom` | **new**: shadow roots walked and observed |
| **closed** shadow root (wrap mode, JS click) | `#closed-host` | `changed`, node `x-closed#closed-host>#shadow-root>span#cn` | **new**: `attachShadow` is hooked by the init script, so closed roots are observed and walked too |
| infinite scroll (scroll → fetch 300 ms → 12 rows appended) | `infinite.html` | `changed`; settlement waits for the fetch (`network.busy_until_ms >= 250`), `settled_by` ∈ network/dom/layout; 12 added, ≤ 12 listed, `element_total` delta 12 | |
| target under a sticky header | `sticky.html#under` | `blocked`, `target_covered_by: header`, dispatch < 1.5 s, `scrolled_into_view_only` | **bug fixed** (bench F1): Playwright's retries scroll the page trying other alignments; that scroll used to make the receipt `changed` |
| target below the fold | `#below` | `changed`; `preflight.prescroll.dy > 0` and `preflight.inViewport: true` (scrolled into view *before* the before-capture), `scroll_dy: 0`, evidence `scrolled_into_view_before_capture`, DOM change listed | **changed** (bench F7): the dispatcher's scroll used to be in the delta and had to be excluded by rule; now it never enters it |
| `pointer-events:none` / `left:-9999px` targets | `buttons.html#pe-none`, `#offscreen` | `blocked` in < 1.5 s with `target_pointer_events_none` / `target_offscreen` | **bug fixed** (bench F2): pre-flight now flags both and shortens the actionability wait; `dispatch.error` carries the reason Playwright buries in its call log |
| hover-revealed menu | `menu.html` | direct click → `blocked` (`target_not_visible`, hint says hover); `hover('#menu')` → `changed` with the items losing their `hidden` signature bit; then click → `changed` | **new**: `hover` tool; `checkVisibility()` in the node signature (5 ms per 24k nodes) |
| hover on an inert element | | `no_op` with a hover-specific hint | |
| `<select>` | `form.html#sel` | `changed` with `value_changed: 'red' -> 'green'`; same option again → `no_op`; missing option → `blocked` in < 0.5 s; by label works | **new**: `select` tool; the option list is checked first, so a missing option does not burn the 3 s actionability timeout |
| `contenteditable` | `#editor` | `changed`, `value_changed: '' -> 'hello world'`, `editable: true` | |
| keyboard only: Tab, type, Tab, Enter, Escape | `keyboard.html` | Tab → `changed` (focus counts for `press`); Enter → `changed` with the result node; Escape → `no_op` naming the key | |
| click that downloads (`<a download>` and a plain link to `Content-Disposition: attachment`) | `download.html` | `changed`, `download_started: report.bin`, hint "do not retry"; settles < 2 s | **bug fixed**: `<a download>` fires *no* network event at all and the plain link's request ends in `net::ERR_ABORTED`, so the receipt used to be `no_op`. A `download` event now releases the request and is evidence |
| `beforeunload` prompt, accepted | `beforeunload.html` | `navigated` with `js_dialog: beforeunload`, hint says it was accepted | |
| `beforeunload` prompt, dismissed (`dialog_action="dismiss"`) | | `changed`, URL unchanged, hint "navigation was cancelled" | |
| CSS transition 300 / 1000 / 2500 ms | `anim.html?ms=` | `changed`, `settled_by: animations`, `ms <= settle < ms + 700`, `animations.busy_until_ms >= ms - 50` | ties in `settled_by` are now broken by a fixed priority, so this is deterministic |
| `setTimeout`-only effect at 150 / 400 / 1500 ms | `late.html?ms=` | with the timers signal **off** (`timer_wait_ms=0`) the old invariant: the change is reported **iff** `timing_ms.observed_window >= ms`, the 1500 ms case is always missed at quiet 100 and says `settled_by: quiet_window, timers.long: [1500]`, at quiet `ms + 300` always caught with `settled_by: dom`; with the signal **on** (default) all three are `changed` at quiet 100, `settled_by: timers|dom`, `ms <= settle < ms + 700` | **bug fixed** (bench F4 / accuracy c39, s07): the timer the action scheduled is now a settlement signal, so the documented `setTimeout` blind spot only remains beyond `timer_wait_ms` (5 s) — see `test_bench_fixes.py` |
| ticker already running (30 ms) | `ticker.html` | `changed` for the counter with **only** `span#c` listed, `nodes_background: ['span#tick']`, `background_mutations_excluded`, settles < 1.5 s; a scroll on the same page → `no_op` with the hint naming the background root | **new** (bench F5): elements that mutated in ≥ 3 batches over ≥ 200 ms in the 1.5 s before the action (after `load`) are background; they neither keep settlement busy nor count in the delta |
| ticker / rAF loop / CSS animation, clicked **right after load** | `bg.html?bg=fast|raf|css` | `no_op` for `#noop`, never `never_settled`; `pre_settle` waited (~300 ms) and named the root; the rAF page masks the moving box from the screenshot diff (`screenshot_masked_regions ≥ 1`), so even a click on non-focusable text is `no_op`; `#counter` on the same page → `changed` with only `span#count` | **bug fixed** (bench k03 / k07): the detector needs 200 ms of cadence *after* `load` and the bench clicked within 60 ms of it; the before-capture now waits (bounded by `pre_settle_ms`) until the page has been observed quiet for `quiet_ms` |
| ticker **started by** the click | `ticker.html?auto=0#start` | `changed`, `never_settled: still busy ['dom'] after 1200ms`, hard timeout honoured (1200 ≤ settle < 1800), no hang | activity that begins with the action cannot be set aside |
| perpetual CSS spinner | `spinner.html` | `changed`, settles < 1.5 s, `animations.infinite: 1`, `animations.background: 1`, never counted busy; a finite transition beside it still settles by `animations` | animations already running at dispatch are background |
| fetch → **transient spinner** → render, at 250 / 1000 / 2000 ms | `twostage.html?delay=` | `changed` with `FINAL:<delay>` in the after-capture (never the spinner), `animations.infinite ≥ 1`, `animations.background: 0`, `animations.busy_until_ms ≥ delay`, settle ≥ 1.5 × delay | **bug fixed** (bench F8, 100/160 stale): an infinite animation that *started after dispatch* is the action's work and is waited on until removed |
| canvas paint on a below-the-fold target | `canvas.html#canvas` | `changed` via `screenshot_changed`, `scroll_dy: 0`, `preflight.prescroll.dy > 0`, `scrolled_into_view_before_capture`; `#silent` next to it → `no_op` (`focus_moved_only`) | **bug fixed** (bench F7): the target is scrolled into view *before* the before-capture, so the scroll never enters the delta and cannot mask a real pixel change |
| timer > `timer_wait_ms` (6 s) | `timers.html#long` | `no_op` in < 1.5 s, `timers.long: [6000]`, evidence `long_timers_not_awaited`; at `timer_wait_ms=7000` the same click is `changed` after ≥ 6 s | the bound is stated, not hidden |
| cleared timer / two chained 200 ms timers | `timers.html#cleared`, `#chain` | cleared at 50 ms → settles < 400 ms; chain → `changed` with `step 2`, settle ≥ 400 | timers are released on `clearTimeout`; a timer created by a foreground timer is foreground |
| Enter on a focused handler-less button (with and without a selector) | `press.html#save` | `no_op`; if the `:active` repaint registers at all it is `screenshot_changed_weak` (< 0.005, dhash 0) | **bug fixed** (bench F9): a 0.002-pixel / dhash-0 delta used to flip the verdict to `changed` — the one verdict a DONE gate keys on |
| Enter that repaints a 300×120 canvas | `press.html#painter` | `changed` via `screenshot_changed` (≥ 0.005, dhash ≥ 1), no DOM change | the perceptual threshold still passes a real repaint |
| `window.open` blocked by the browser (`--block-new-web-contents`) vs allowed | `popup.html` | blocked: `changed` (status text), `new_tabs: []`; allowed: `navigated` with `new_tab_opened` | Playwright's normal launch can never block a popup |
| form POST → HTTP 400 page | `form-post.html#submit-nav` | `navigated`, `http_error: 400 POST /submit`, hint "HTTP 400", title "Bad Request" | **new**: responses ≥ 400 after dispatch are evidence |
| `fetch` → 400, body never read | `#submit-fetch` | `changed`, settles < 1.5 s, `network.released` says the small body was already buffered | **bug fixed**: Chromium never emits `requestfinished` for an unread body, so this hung until the 8 s timeout. Small `Content-Length` bodies are released at the headers |
| `fetch`, body never read, **no** `Content-Length` | `#fetch-nolen` | `changed`, `settled_by: network` after `body_grace_ms` (700 in the test), `released` says "not finished within 700ms" | the bound is stated, not hidden |
| double-click (`click_count=2`) | `buttons.html#dbl-btn` | one `changed` receipt with both the click counter (2) and the `dblclick` effect | |
| two rapid single clicks | `#counter-btn` ×2 | two receipts, `t=0→1` then `t=1→2`, distinct ids | |
| 6,000 rows = 12,006 elements | `big.html?n=6000` | not truncated; 100 edits → `nodes_changed_total: 100`, 12 listed; captures < 600 ms each incl. screenshot; the receipt < 20 kB; an edit near the end is named | **bug fixed**: the walk was O(n²) (per-node `cssPath` iterated siblings) and the 10k-array transfer dominated — 4k nodes cost 350 ms, 12k cost 2.7 s. Now incremental paths + one joined string: ~90 ms at 20k elements |
| 15,000 rows = 30,006 elements (past the 20k cap) | `big.html?n=15000` | `truncated`, `fingerprint_truncated: {20000, 30006}` in delta and evidence; the edit past the cap is still `changed` via `text_changed` but cannot be named | documented limit |

## Robustness (`test_robustness.py`)

| case | proves |
|---|---|
| CDP attach to our own Chromium (`--remote-debugging-port=0`, port read from `DevToolsActivePort`) | default context shared, receipts work, closing the session leaves the browser running |
| wrap mode with an **independent** `connect_over_cdp` client doing the click | `receipt_begin`/`receipt_end` see the other client's action; a wrapped nothing is `no_op` |
| `--cdp-new-context` with a "user" tab open | pre-existing targets (ids + URLs, read from `/json/list`) are byte-identical during and after; only our tab appears and disappears |
| page A + page B (ticker) in one browser | A's receipt sees none of B's mutations / background roots; B's own receipt attributes its ticker as background |
| closed tab | `unknown`, `page_closed` first in evidence, `settlement.aborted`, both captures `ok: false`, < 3 s, session still usable |
| crashed tab (`chrome://crash`) | `unknown`, `page_crashed`, `delta.page_crashed`, < 4 s, session still usable | **bug fixed**: settlement used to spin on `Target crashed` evaluate errors until the timeout |

## Schema invariants (`schema.py`, exercised by `test_schema.py`)

Strict models (`extra="forbid"`) for the whole receipt; on top of types: evidence ≥ 1 item;
`no_op`/`blocked` carry a hint; `timed_out` ⇒ non-empty `busy_at_timeout`, not `settled`,
elapsed ≥ 95 % of the timeout, and `never_settled` in the evidence; `aborted` ⇒ `unknown`;
node lists only with `dom_changed`, never longer than the totals, ≤ 12; `url_changed` and
`url_only_fragment` exclusive; `navigated` ⇒ a navigation in the delta; `no_op` ⇒ no
substantive delta; `truncated` ⇒ `element_total > element_count`; `timing_ms` carries
`observed_window` and `total`.

## Known limits the suite documents (with the measured numbers)

All measured on 2026-09-02 on AIServer (headless Chromium 151, 1000×600 viewport, the
suite's own fixture server, with the benchmark suite running concurrently on the same box —
so these are loaded-machine numbers, not best case). Reproduce with the tests named above;
the raw run is `measure.json` in the session scratchpad.

**Capture cost (the DOM walk is O(n); transfer is one string, not n arrays)**

| rows | elements | fingerprinted | walk only, median (min) | walk + viewport screenshot, median |
|---|---|---|---|---|
| 1,500 | 3,006 | 3,006 | 14 ms (13) | 37 ms |
| 3,000 | 6,006 | 6,006 | 53 ms (35) | 104 ms |
| 6,000 | 12,006 | 12,006 | 106 ms (100) | 155 ms |
| 10,000 | 20,006 | 20,000 (cap) | 180 ms (116) | 223 ms |
| 15,000 | 30,006 | 20,000 (cap) | 110 ms (77) | 165 ms |

Before the fix the same walk cost 350 ms at the old 4,000-node cap and 2.7 s at 12k elements.
A no-op receipt on `short.html` is 214 ms end to end (capture 48 + dispatch 14 + settle 89 +
capture 45; `observed_window` 161 ms).

**Settlement waits for what it can see**

| page | settled_by | settle | note |
|---|---|---|---|
| CSS transition 300 / 1000 / 2500 ms | `animations` | 330 / 1032 / 2541 ms | `animations.busy_until_ms` 303 / 1004 / 2518 |
| 1.5 s fetch then DOM update | `dom` | 1618 ms | network busy until 1483 ms, then the DOM update restarts the quiet window |
| infinite scroll (fetch 300 ms → 12 rows) | `dom` | 442 ms | 12 added, 12 listed |
| `fetch` → 400, body never read (108-byte `Content-Length`) | `dom` | 132 ms | released at the headers: "body of 108 bytes is already buffered" — was an 8 s timeout before |
| `fetch`, body never read, no `Content-Length` | `network` | 807 ms at `body_grace_ms=700` | released: "body not finished within 700ms" — the bound is stated |
| 30 ms ticker already running, click a counter | `dom` | 113 ms | `changed` with only `span#c` listed; `span#tick` background, 5 background mutations during the window |
| 30 ms ticker **started by** the click | timeout | 1206 ms at `timeout_ms=1200` | `never_settled: still busy ['dom'] after 1206ms` — an effect that never stops cannot be set aside |
| perpetual CSS spinner, click a counter | `dom` | 121 ms | `animations.infinite: 1`, never busy |
| `<a download>` / attachment link | `quiet_window` | 69 / 68 ms | `download_started`, no hang |

**The quiet-window bound, stated per receipt** (`late.html`, a `setTimeout` DOM change with
nothing else to watch). The first two columns are measured with the timers signal switched
off (`timer_wait_ms=0`) — the pre-fix behaviour, kept as the documented bound; the third is
the default.

| timer | quiet 100 ms, timers off | quiet timer + 300 ms, timers off | quiet 100 ms, timers on (default) |
|---|---|---|---|
| 150 ms | `no_op`, `settled_by: quiet_window`, settle 67 ms, `observed_window` **150 ms** — missed by a hair, and the receipt says how far it looked (`timers.long: [150]`) | `changed`, `settled_by: dom`, settle 621 ms | `changed`, `settled_by: dom`, settle ≈ 250 ms |
| 400 ms | `no_op`, settle 68 ms, `observed_window` 144 ms | `changed`, `dom`, settle 1105 ms | `changed`, `dom`, settle ≈ 500 ms |
| 1500 ms | `no_op`, settle 72 ms, `observed_window` 167 ms | `changed`, `dom`, settle 3311 ms | `changed`, `dom`, settle ≈ 1600 ms |

No finite wait can beat a timer longer than itself — unless the timer is observed. The
`timers` signal observes it: a `setTimeout` the action scheduled keeps settlement busy until it
fires (bounded by `timer_wait_ms`, 5 s; longer ones are listed as `long_timers_not_awaited`),
so the effect is waited for exactly as long as it takes plus the quiet window. The 150 ms row
with timers off is the one genuinely racy case at quiet 100 (the timer lands during the
after-capture on a loaded machine), which is why that arm of the test asserts *consistency* —
the change is reported iff `observed_window >= timer` — rather than one outcome.

**What the pre-settle and pre-scroll steps cost** (`test_pre_settle_cost_is_bounded_by_the_quiet_window_and_zero_once_the_page_has_been_seen_quiet`)

| situation | pre-settle | note |
|---|---|---|
| first action within 100 ms of `load`, quiet page | ≤ 100 ms (the rest of one quiet window) | the page has not been watched for a full window yet |
| any later action on a quiet page | one evaluate, < 40 ms (typically 1–3 ms) | `polls: 1` |
| first action after `load` on a 50 ms ticker / rAF page | ~300 ms | ≥ 3 batches over ≥ 200 ms, then a 100 ms quiet window with the root set aside |
| page still mutating at the cap | `pre_settle_ms` (800) | recorded as `quiet: false`; settlement proceeds as before |
| pre-scroll, target in view | 0 (one probe, ~2 ms) | `preflight.prescroll` absent |
| pre-scroll, target off-screen | ~2 ms | instant `scrollIntoView`, recorded as `preflight.prescroll {dx, dy}` |

**Blocked verdicts are cheap**

| target | verdict | dispatch | total receipt |
|---|---|---|---|
| disabled / covered / `pointer-events:none` / offscreen | `blocked` | ~301 ms (the shortened actionability wait) | 408–468 ms |
| under a sticky header (Playwright retries scroll 30 px) | `blocked` | 301 ms | `scrolled_into_view_only: dy=30` |
| `<select>` option that does not exist | `blocked` | **7 ms** | no actionability wait at all |
| closed tab | `unknown` | — | **5 ms** (was the full settle timeout) |
| crashed tab | `unknown` | — | **9 ms** (was the full settle timeout) |

## 10× stability run (2026-09-02, sequential, fresh process each time)

```
run 1  74 passed in 75.20s      run 6  74 passed in 99.70s
run 2  74 passed in 77.53s      run 7  74 passed in 105.30s
run 3  74 passed in 102.77s     run 8  74 passed in 89.76s
run 4  74 passed in 107.71s     run 9  74 passed in 93.65s
run 5  74 passed in 99.92s      run 10 74 passed in 103.93s
```

740/740, zero flakes. The 75–108 s spread is CPU contention from the concurrent benchmark
suite, which makes this a loaded-machine result rather than a quiet one.

## 5× stability run after the bench fixes (2026-09-02, sequential, fresh process each time)

```
run 1  93 passed in 113.71s      run 4  93 passed in 113.90s
run 2  93 passed in 114.22s      run 5  93 passed in 113.79s
run 3  93 passed in 113.89s
```

465/465, zero flakes (a previous 5× at 91 tests, before F9 was folded in, was 455/455). The
benchmark suite was running concurrently on the same box for part of it.
