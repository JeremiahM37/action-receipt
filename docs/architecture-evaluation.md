# Is Playwright the right substrate? A measured answer

*2026-09-03. Three spikes, all reproducible from
`star-research/execution/A1/spikes/{coverage,overhead,reach}/` (scripts, fixtures, raw numbers).*

The question was whether the receipts would be better - see more, cost less, reach more users -
if the tool were built on raw CDP, as a browser extension, or from scratch, instead of on
Playwright. The short answer: **the oracle is already ours (the injected script), Playwright is
the driver, and every measured improvement is on top of that split, not instead of it.** What
follows is the evidence.

## 1. What the current design cannot see, and which layer closes each gap

Fifteen effects that a real page produces and that the DOM-fingerprint + screenshot receipt
might miss, each with a ground truth that *does* happen:

| # | effect | today | closes with | cost |
|---|---|---|---|---|
| 1 | text change inside a **cross-origin iframe** | miss | Playwright `Frame.evaluate` of the same capture/status scripts per frame (Playwright drives OOPIFs as CDP targets) | +4-5 ms/frame |
| 2 | compositor-only CSS / `element.animate()` started by the click | **seen** (`settled_by: animations`, waited 1.5 s) | - | - |
| 3 | Web Worker reply updates the DOM after 800 ms | miss | injected `Worker.prototype.postMessage` hook as a settlement signal | 0 ms/poll |
| 4 | WebSocket reply after 700 ms | miss | `page.on("websocket")` frames sent-vs-received (Playwright) | effect latency |
| 5 | Server-Sent Event after 600 ms | miss | CDP `Network.eventSourceMessageReceived` (Playwright never surfaces it) | effect latency |
| 6 | service-worker-answered fetch | **seen** | - | - |
| 7 | canvas 2D / WebGL change below the perceptual floor | miss | per-canvas clipped screenshot (CDP clip 124 ms vs Playwright locator 276 ms); `toDataURL` is blind to WebGL | ~124 ms when a canvas is the target |
| 8 | `requestIdleCallback` effect | **seen** | - | - |
| 9 | native `<select>` popup | miss (the 0.67 % pixel change was real but discounted as focus) | CDP `Accessibility.getPartialAXTree` -> `expanded: true` | 1.4 ms |
| 10 | same-origin `postMessage` into a frame | **seen** | - | - |
| 11 | model-only contenteditable (`beforeinput` without `input`) | miss | injected `beforeinput` listener | 0.7 ms |
| 12 | effect only in localStorage / IndexedDB / cookie | miss | injected storage hooks | 0.4 ms |
| 13 | acknowledged POST, DOM unchanged | miss (the receipt *named* `/ack` and then discarded it) | count a non-GET 2xx as an effect | 0 |
| 14 | shadow root inside a cross-origin iframe | miss | same as #1 (the `attachShadow` hook already runs in the frame) | as #1 |
| 15 | popup opened, then changes 625 ms later | partial (`navigated`, content change not seen) | follow the popup's own receipt | +528 ms |

**Sees 4 of 15 fully today.** Nine of the ten misses close with Playwright's own API or more
injected JavaScript; two need a CDP call, which Playwright issues through
`context.new_cdp_session(page)`; **none needs an extension or a custom browser.** The only
thing out of reach for any out-of-process observer is a native date picker's open state, and
even that is painted into the screenshot. `DOMSnapshot.captureSnapshot` was tested as the
"see everything" alternative: it does see cross-origin frames, but it excludes out-of-process
frames and is 1.3-13x slower with a spiky p90.

## 2. Where the milliseconds go, and whether raw CDP changes that

Click receipt today, p50 of 30 interleaved runs: 167 ms on a tiny page, 276 / 284 / 297 ms on
GitHub (2.9k elements) / Wikipedia (4.2k) / a 20k-element page. Two captures are ~58 % of that,
the 100 ms quiet window ~32 %, dispatch ~8 %, delta + verdict under 2 %.

Inside a capture the walk itself is 9 / 14 / 39 ms; the other 20-35 ms is V8 serialising a
~700 KB node string that is byte-identical before and after on every no-op. Transport is not
the cost: `page.evaluate` 45.4 ms, a CDP session 48.4 ms, a bare WebSocket client with no
Playwright and no Node 44.5 ms on the same call. Polling is 2-3 ms per receipt in total.

Three changes, all on the existing path, measured separately:

| change | effect |
|---|---|
| diff the fingerprint **in the page** (keep the previous map in `window`, return hash + bounded node lists, ~800 B) | capture 43.6 -> 12.0 ms (GitHub), 51.2 -> 18.1 (Wikipedia), 56.6 -> 45.8 (20k) |
| JPEG quality-40 screenshots instead of PNG | ~3x cheaper, identical signal on every arm tested |
| event-driven quiet wait instead of polling | -15 to -30 ms per action, 5-10 % less CPU |

Additive estimate, not yet run as one receipt: **~100 ms tiny / 150 GitHub / 160 Wikipedia /
220 on 20k elements**, keeping every current signal.

Raw CDP dispatch is 0.4-0.9 ms against Playwright's 35 ms - but it returns "no error" for a
disabled, covered, `pointer-events: none`, off-screen or `display: none` target, so every
`blocked` verdict would rest on the pre-flight probe alone. A raw-CDP rewrite saves 0-1 ms
per capture and 33 ms per click only by giving up the signals `blocked` is made of.

## 3. Reach: what Playwright buys, and what an extension would add

Cross-browser, integration tier, engine-relevant items only: **Chromium 101/101, Firefox 91/95,
WebKit 90/95.** Two of the failures are real settlement bugs that Chromium happens to hide (a
timer-scheduled navigation can land after settlement returns; an iframe's `about:blank` window
being reused defeats the install guard). One is a Chromium-only API (`layout-shift`
PerformanceObserver) already wrapped in try/catch. The rest are assertion wording. Roughly a
day gets both engines to 100/101. Worth having as an informational CI job, not as a promise:
browser-use, Stagehand, Skyvern and Claude in Chrome are Chromium-only; only playwright-mcp
offers Firefox/WebKit. Wrap mode and CDP attach are Chromium-only by construction.

An MV3 extension carrying the same injected oracle (extracted verbatim at build time) matched
the library's verdicts on **10 of 11** fixture cases, settling within +/-10 ms, and reaches
cross-origin frames *better* than the library does today. Its costs: trusted input, dialogs and
crash detection need `chrome.debugger`; `captureVisibleTab` is quota-limited to 2 per second,
so the perceptual signal caps receipts at about one per second; the MCP bridge needs a
native-messaging host per OS. Estimate 10-12 days to fixture parity, as a **second package
sharing the generated `inject.js`**, for the audience that drives a user's own Chrome. Not a
rewrite.

## Verdict

1. **Keep Playwright as the driver.** From scratch or raw CDP buys ~1 ms per capture and loses
   the actionability engine that produces `blocked`, cross-browser reach, and downloads,
   dialogs, popups and network events that would all have to be rebuilt.
2. **The gains are in the oracle, which is already ours.** Nine of ten coverage gaps close with
   more injected hooks and per-frame evaluation; two with CDP calls Playwright already exposes.
3. **The overhead gains are three local changes**, roughly halving per-action cost.
4. **An extension is a distribution decision, not an architecture one**: same oracle, second
   package, when the user's-own-Chrome audience is real.

Planned as 0.4.0, in this order: per-frame observe/capture including cross-origin frames (also
fixes the Firefox iframe bug); WebSocket / worker / SSE as settlement signals; storage, POST-2xx
and `beforeinput` as evidence; in-page diff + JPEG + event-driven wait; the two settlement races;
`<select>` popup via the partial AX tree; canvas clip screenshots when a canvas is the target.
