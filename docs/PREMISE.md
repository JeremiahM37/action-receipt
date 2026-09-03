# Premise check — verified against today's source (2026-09-02)

Every claim below was read from a fetched file or a `gh api` response on 2026-09-02, not
from memory. Commit hashes are the `main` heads at the time of the fetch. Where the
premise in `GOAL.md` / the cycle-6 seam doc turned out to be wrong or weaker than stated,
this file says so.

Companion record: `execution/A1/VERIFIED-CLAIMS.md` (written independently from local clones
pinned at the same browser-use commit `564007d`) reaches the same Q1 conclusion line-for-line,
adds that the UI-TARS *model* repo (`bytedance/UI-TARS`, `codes/ui_tars/action_parser.py:459-466`)
emits `pyautogui.scroll()` which returns `None` — no result object at all — and reads Skyvern's
shipped verification as an LLM/screenshot judge. Nothing there contradicts this file; where the
two overlap they agree.

Summary table:

| # | Question | Verdict |
|---|---|---|
| 1 | browser-use default scroll reports success when nothing moved | **TRUE** on master `564007d` — and nothing in the whole dispatch chain reads the scroll position |
| 2 | #5361 / #5438 open; has an invariant been merged? | **Both open, no maintainer reply, 9 fix PRs open, 0 merged.** A models-only "action evidence" PR (#5145) with a verdict set nearly identical to ours is open and unwired. Risk is live, not realized |
| 3 | UI-TARS-desktop #1952 open? shape? | **Open.** Un-awaited async ADB calls: `execute()` returns `success` before the device acts. A *settlement* failure, not a *delta* failure |
| 4 | Skyvern #3099 "ships it as a feature" | **Wrong number.** #3099 is a changelog issue. The real one is **PR #3080** (merged 2025-08-04) — a 6-line *frontend* change that styles `skipped` actions as successes. Weaker than the seam doc implied |
| 5 | Post-action magic constants 2.0 / 0.0 / 0.5 | **TRUE for Anthropic (2.0) and OSWorld (0.0 and 0.5).** browser-use's 0.5 is a declared profile field I could not find applied; its live inter-action wait is **0.1 s**. playwright-mcp adds a fourth: **500 ms** |
| 6 | Does playwright-mcp already return a state delta? | **Partly.** It has a request-drain settlement (anchored on two 500 ms sleeps) and a url/title/console `changed` boolean used only for rendering. **No diff, no scroll delta, no no-op signal, no verdict.** The receipt is additive |

---

## Q1 — browser-use: does the default scroll still report success when nothing scrolled?

**Repo head**: `browser-use/browser-use` `main` = `564007d3d64cda0cccd29f37d70f8cfe9bebcd62`
(committed 2026-09-01T18:51:05Z).

**`ScrollAction.pages` defaults to `1.0`** —
https://github.com/browser-use/browser-use/blob/564007d3d64cda0cccd29f37d70f8cfe9bebcd62/browser_use/tools/views.py#L130-L133

```python
class ScrollAction(BaseModel):
	down: bool = Field(default=True, description='down=True=scroll down, down=False scroll up')
	pages: float = Field(default=1.0, description='0.5=half page, 1=full page, 10=to bottom/top')
	index: int | None = Field(default=None, description='Optional element index to scroll within specific element')
```

**`completed_scrolls` is never consulted for the returned `ActionResult`** —
https://github.com/browser-use/browser-use/blob/564007d3d64cda0cccd29f37d70f8cfe9bebcd62/browser_use/tools/service.py#L1408-L1470

```python
				if params.pages >= 1.0:                                   # L1408
					...
					completed_scrolls = 0                                 # L1414
					for i in range(num_full_pages):                       # L1417
						try:
							...
							await event.event_result(raise_if_any=True, raise_if_none=False)
							completed_scrolls += 1                        # L1428
							await asyncio.sleep(0.15)
						except Exception as e:
							logger.warning(f'Scroll {i + 1}/{num_full_pages} failed: {e}')
							# Continue with remaining scrolls even if one fails     # L1435
					...
					if params.pages == 1.0:                                # L1454
						long_term_memory = f'Scrolled {direction} {target} {viewport_height}px'.replace('  ', ' ')
					else:
						long_term_memory = f'Scrolled {direction} {target} {completed_scrolls:.1f} pages'.replace('  ', ' ')
				...
				msg = f'🔍 {long_term_memory}'
				logger.info(msg)
				return ActionResult(extracted_content=msg, long_term_memory=long_term_memory)   # L1470
```

Three observations, all from the quoted code:

1. On the **default** path (`pages == 1.0`, L1454) `completed_scrolls` is not even used for the
   message — the memory string says `Scrolled down 1000px` unconditionally.
2. On the non-default path it is only interpolated into a string; it never gates `error=`.
   `ActionResult(...)` at L1470 is returned without `error` whatever `completed_scrolls` is.
3. Per-page exceptions are swallowed with the comment *"Continue with remaining scrolls even
   if one fails"* (L1435), so even a raised failure inside the loop still returns success.

**Deeper: nothing in the chain reads the scroll position.** The event handler
`on_ScrollEvent` (`browser_use/browser/watchdogs/default_action_watchdog.py` L513) calls
`await self._scroll_with_cdp_gesture(pixels)` at **L559 and discards its return value**.
`_scroll_with_cdp_gesture` (L2204) returns `True` as soon as
`Input.synthesizeScrollGesture` is *accepted* by CDP (L2237-L2249) — it never reads
`scrollY`/`scrollTop` before or after. Its docstring promises a JS fallback on `False`, but
the caller at L559 ignores `False`, so no fallback runs either.
https://github.com/browser-use/browser-use/blob/564007d3d64cda0cccd29f37d70f8cfe9bebcd62/browser_use/browser/watchdogs/default_action_watchdog.py#L513-L568
https://github.com/browser-use/browser-use/blob/564007d3d64cda0cccd29f37d70f8cfe9bebcd62/browser_use/browser/watchdogs/default_action_watchdog.py#L2204-L2250

**Verdict: the premise holds exactly as stated.** A scroll on a page that cannot scroll
returns a non-error `ActionResult` with memory text `Scrolled down 1000px`.

## Q2 — #5361 / #5438 state; has anything merged that generalizes "verify the effect"?

Fetched via `gh api repos/browser-use/browser-use/issues/<n>` and `/timeline`, 2026-09-02.

| Issue | State | Title | Created | Last activity | Comments | Maintainer reply |
|---|---|---|---|---|---|---|
| [#5361](https://github.com/browser-use/browser-use/issues/5361) | **open** | Bug: scroll/input/scroll_to_text report failed actions as successes, so multi_act never aborts and consecutive_failures never increments | 2026-08-03 | 2026-08-17 | 3 | **none** (all `author_association: NONE`) |
| [#5438](https://github.com/browser-use/browser-use/issues/5438) | **open** | Bug: click, dropdown_options, and select_dropdown report 'element not found' as success instead of error | 2026-08-10 | 2026-08-17 | 7 | **none** |
| [#5137](https://github.com/browser-use/browser-use/issues/5137) | **open** | Proposal: add structured action evidence for no-op and changed browser actions | 2026-07-04 | 2026-08-17 | 7 | **none** |
| [#5486](https://github.com/browser-use/browser-use/issues/5486) | **open** | Bug: switch action reports a successful tab switch when the switch failed (error= never set) | 2026-08-17 | — | — | — |

#5486 is a **fourth** instance of the same bucket, filed after the seam doc was written.

**PRs cross-referenced from those issues (all fetched; state as of 2026-09-02):**

| PR | For | State | Merged | Files | Reviews |
|---|---|---|---|---|---|
| [#5374](https://github.com/browser-use/browser-use/pull/5374) | #5361 | open | no | — | — |
| [#5381](https://github.com/browser-use/browser-use/pull/5381) | #5361 | open | no | `tools/service.py` + test | bots only (copilot, cubic-dev-ai) |
| [#5390](https://github.com/browser-use/browser-use/pull/5390) | #5361 | open | no | — | — |
| [#5441](https://github.com/browser-use/browser-use/pull/5441) | #5361 | open | no | `tools/service.py` + test | bot only |
| [#5440](https://github.com/browser-use/browser-use/pull/5440) | #5438 | open | no | — | — |
| [#5455](https://github.com/browser-use/browser-use/pull/5455) | #5438 | open | no | — | — |
| [#5456](https://github.com/browser-use/browser-use/pull/5456) | #5438 | open | no | — | — |
| [#5457](https://github.com/browser-use/browser-use/pull/5457) | #5438 | open | no | — | — |
| [#5487](https://github.com/browser-use/browser-use/pull/5487) | #5486 | open | no | — | — |
| [#5145](https://github.com/browser-use/browser-use/pull/5145) | #5137 | open | no | `agent/views.py` + `tests/ci/test_action_evidence.py` (+370/-0), `mergeable_state: behind` | bot only |
| [#5215](https://github.com/browser-use/browser-use/pull/5215) | #5137 | open | no | `agent/views.py` + test (+99/-0) | bot only |
| [#5214](https://github.com/browser-use/browser-use/pull/5214) | #5137 | closed | **no** | — | — |

**Nine call-site fix PRs are open; zero are merged; no human reviewer has touched any of
them.** The only commit to `browser_use/tools/service.py` since 2026-07-01 is
`c9dae1b6` (2026-08-03, *"fix: include exception details in coordinate click and add
missing error logs"*), while `main` received 100+ commits since 2026-08-01 — the file is not
frozen, the bucket is simply unowned.

**The generalization attempt exists and it is close to this design.** PR #5145 adds to
`browser_use/agent/views.py`:

```python
ActionEvidenceOutcome = Literal['changed', 'no_change', 'navigation', 'failed', 'attention', 'unknown']
...
class PageFingerprintDelta(BaseModel):
	"""Fingerprint-level delta between two page observations."""
	url_changed: bool = False
	text_changed: bool = False
	element_count_delta: int | None = None
	observed_delta: list[str] = Field(default_factory=list)
```

plus a `NO_CHANGE_RECOVERY_HINT`. That is a verdict enum, a delta and a hint — the shape of
this project. It is **models only**: it touches no tool, no watchdog, and no `ActionResult`
producer, so nothing on master emits it. It has been `behind` since 2026-07-29.

**Honest read of the absorption risk:** not realized as of today, but the community has
already drafted the invariant *inside* browser-use, and the four-phase vocabulary this
project uses (dispatch → settlement → delta → verdict) was articulated by contributors in
#5137 (comments 2026-07-05/06). If a maintainer merges and wires #5145, the browser half of
this project is absorbed for browser-use users. The counter-argument is structural:
browser-use's fix can only ever be inside browser-use; it does not help playwright-mcp,
Anthropic's loop, Skyvern, UI-TARS, or OSWorld, which is why this is an MCP server and not a
browser-use PR. Watch `browser_use/agent/views.py` and `tools/service.py` for the enum above.

## Q3 — UI-TARS-desktop #1952

https://github.com/bytedance/UI-TARS-desktop/issues/1952 — **open**, created 2026-08-22,
last activity 2026-08-23, 1 comment. Title: *"[Bug Report]: ADB operator reports success
before asynchronous actions finish"*. Against `main` `c2ad42e3eb9b27830db41a3e6f51ca7179d9b168`,
`@gui-agent/operator-adb` 0.3.0.

Shape (quoted from the body): in `multimodal/gui-agent/operator-adb/src/AdbOperator.ts`,
`long_press`, `swipe`/`drag`, `scroll`, `type` and `handleHotkey()` call async ADB helpers
*without `await`*, so *"`execute()` can report success before the device action finishes,
and a later rejection can become detached from the action request."* Impact quoted:
*"screenshots may be taken before the preceding gesture/type completes; and device command
failures may surface as unhandled rejections instead of an operator error."*

This is a **settlement** failure (success reported at dispatch time), not a **delta**
failure (no check that anything changed). Both are covered by a receipt, but they are
different stages, and the doc should not describe #1952 as "the same bug" as #5361 — it is
the same *consequence* (agent reasons from a false success) from a different stage.

## Q4 — Skyvern #3099

**The number in GOAL.md / the seam doc is wrong.**
https://github.com/Skyvern-AI/skyvern/issues/3099 is *"Weekly PR Changelog Report"*
(closed `not_planned` 2026-02-05) — a bot-authored changelog that merely *lists* the real
change.

The real change is **PR #3080** — https://github.com/Skyvern-AI/skyvern/pull/3080 —
*"skipped action should mark as success"*, **merged 2025-08-04T02:49:45Z**
(merge `f2510bf2b70e0032e40bb2be98454549d69f1024`), 3 files, +6/−2, all in
`skyvern-frontend/`:

```ts
// skyvern-frontend/src/api/types.ts
+  Skipped: "skipped",
// skyvern-frontend/src/routes/tasks/detail/hooks/useActions.ts
-            success: action.status === Status.Completed,
+            success:
+              action.status === Status.Completed ||
+              action.status === Status.Skipped,
// skyvern-frontend/src/routes/workflows/workflowRun/ActionCard.tsx
-  const success = action.status === Status.Completed;
+  const success =
+    action.status === Status.Completed || action.status === Status.Skipped;
```

So: it *is* a merged feature that renders a not-executed action with success styling, and
the backend keeps a distinct `skipped` status. It is a **UI** decision, not a change to what
the agent loop is told. The seam doc's "ships it as a FEATURE" is literally true; the
implication that Skyvern's *agent* is lied to is not supported by this PR and must not be
claimed.

## Q5 — the post-action "magic constants"

| System | Value | Where |
|---|---|---|
| Anthropic computer-use reference | **2.0 s** | `anthropic-quickstarts` `main` `3313e9716fb5b977248bcd06cb0cc86a8c547b9b`, `computer-use-demo/computer_use_demo/tools/computer.py` **L99** `_screenshot_delay = 2.0`; **L273-274** `# delay to let things settle before taking a screenshot` / `await asyncio.sleep(self._screenshot_delay)` — https://github.com/anthropics/anthropic-quickstarts/blob/3313e9716fb5b977248bcd06cb0cc86a8c547b9b/computer-use-demo/computer_use_demo/tools/computer.py#L99 and https://github.com/anthropics/anthropic-quickstarts/blob/3313e9716fb5b977248bcd06cb0cc86a8c547b9b/computer-use-demo/computer_use_demo/tools/computer.py#L273-L274 |
| OSWorld `run.py` | **0.0** | `xlang-ai/OSWorld` `main` `fc31a9049664292fcb35d6e501ee1dc839f2cf6d`, `run.py` **L96** `parser.add_argument("--sleep_after_execution", type=float, default=0.0)` — https://github.com/xlang-ai/OSWorld/blob/fc31a9049664292fcb35d6e501ee1dc839f2cf6d/run.py#L96 ; same default in `scripts/python/run_multienv.py` L62 |
| OSWorld Claude runner | **0.5** | `scripts/python/run_multienv_claude.py` **L63** `default=0.5` — https://github.com/xlang-ai/OSWorld/blob/fc31a9049664292fcb35d6e501ee1dc839f2cf6d/scripts/python/run_multienv_claude.py#L63 |
| OSWorld env itself | **2** (a third value) | `desktop_env/desktop_env.py` **L416** `def step(self, action, pause=2):` … **L453** `time.sleep(pause)` then `observation = self._get_obs()` — https://github.com/xlang-ai/OSWorld/blob/fc31a9049664292fcb35d6e501ee1dc839f2cf6d/desktop_env/desktop_env.py#L416-L454 . The runners pass their own value in via `env.step(action, args.sleep_after_execution)` (`lib_run_single.py` L40/L131/L413) |
| browser-use (inter-action) | **0.1 s** | `browser_use/browser/profile.py` **L694** `wait_between_actions: float = Field(default=0.1, ...)`; applied at `browser_use/agent/service.py` **L2769-2770** `await asyncio.sleep(self.browser_profile.wait_between_actions)` — https://github.com/browser-use/browser-use/blob/564007d3d64cda0cccd29f37d70f8cfe9bebcd62/browser_use/browser/profile.py#L694 and https://github.com/browser-use/browser-use/blob/564007d3d64cda0cccd29f37d70f8cfe9bebcd62/browser_use/agent/service.py#L2769-L2770 |
| browser-use (network idle) | **0.5 s declared** | `profile.py` **L692** `wait_for_network_idle_page_load_time: float = Field(default=0.5, description='Time to wait for network idle.')`. **Caveat**: `gh api search/code` finds it only in `profile.py`, `session.py` (constructor pass-through at L185-186 / L219-220 / L325-326), `beta/service.py` (env-var mapping L954-956) and docs. I could not find a call site on this commit that *applies* it. GOAL.md's "browser-use 0.5 s" is a real default but I cannot confirm it is live; the wait that demonstrably runs per action is 0.1 s |
| playwright-mcp | **500 ms** (×2) | `microsoft/playwright` `main` `350d24a344b07543fdc4014339a7871fd1c1b227`, `packages/playwright-core/src/tools/backend/utils.ts` **L21** `const settleMs = tab.context.config.timeouts?.settle ?? 500;` — https://github.com/microsoft/playwright/blob/350d24a344b07543fdc4014339a7871fd1c1b227/packages/playwright-core/src/tools/backend/utils.ts#L20-L55 ; documented in `src/tools/mcp/config.d.ts` L215-217 *"How long to wait after each action for triggered work (navigations, requests) to settle before responding. Defaults to 500ms."* |

Four systems, five constants (2.0 / 0.0 / 0.5 / 2 / 0.1 / 0.5), none measured. The premise
("three values, none measured") understates it.

## Q6 — does playwright-mcp already return a post-action state delta?

**Where the source lives now**: `microsoft/playwright-mcp` at `4c1fb03bad3bae379b0ae0e3d81d2660de56bd91`
is a shim — `package.json` `@playwright/mcp` `0.0.80`, only 10 `.ts` files, all tests and
config. The implementation is in the `microsoft/playwright` monorepo under
`packages/playwright-core/src/tools/backend/` (head `350d24a344b07543fdc4014339a7871fd1c1b227`).

**Settlement — exists, in a weaker form.** `utils.ts` L20-55 `waitForCompletion`:
records every `request` fired during the action; sleeps `settleMs` (500); if any recorded
request `isNavigationRequest()`, waits `mainFrame().waitForLoadState('load', {timeout: 10000})`;
otherwise waits for the recorded `document/stylesheet/script/xhr/fetch` responses to
`finished()` (raced against a 5 s timeout) and then sleeps `settleMs` again. Real network
drain, but: (a) it is still bracketed by two blind 500 ms sleeps, (b) it does not observe DOM
mutations, animations, layout shift, or scroll motion, (c) it does not report *which* signal
settled or how long it took.
https://github.com/microsoft/playwright/blob/350d24a344b07543fdc4014339a7871fd1c1b227/packages/playwright-core/src/tools/backend/utils.ts#L20-L55

**Delta — does not exist.** After the action the response attaches a fresh full aria snapshot
(`response.setIncludeSnapshot()` in every action tool, e.g. `mouse.ts` L125, `snapshot.ts`
L75/L116/L142/L166), the `Modal state` section (`response.ts` L300-301), and console counts.
The only before/after comparison is `tab.ts` L291-313 `headerSnapshot()`, which compares
`{title, url, current, crashed, mainDocumentStatus, console counts}` against the previous
header and returns `changed: true|false` — and `response.ts` L293 uses that boolean **only
to decide whether to render the "Page"/"Open tabs" section**; it is not a field the model
receives. `grep -i 'diff|nothing changed|no change|unchanged|no-op|noop'` across
`response.ts snapshot.ts common.ts tools.ts tab.ts navigate.ts mouse.ts keyboard.ts wait.ts`
returns **zero hits**.
https://github.com/microsoft/playwright/blob/350d24a344b07543fdc4014339a7871fd1c1b227/packages/playwright-core/src/tools/backend/tab.ts#L291-L313
https://github.com/microsoft/playwright/blob/350d24a344b07543fdc4014339a7871fd1c1b227/packages/playwright-core/src/tools/backend/response.ts#L288-L301

**Verdict: the receipt is additive.** playwright-mcp gives the model a *new* snapshot and
expects the model to notice what changed. It never says "nothing changed", never reports a
scroll delta, never diffs the tree, and its settlement is not measured. The `wrap` mode of
this project (`receipt_begin`/`receipt_end` over a shared CDP session) exists precisely so a
playwright-mcp user can add the missing layer without replacing their tools.

## The cited paper

arXiv **2507.11662** exists. Title (from the arXiv API, published 2025-07-15):
**"Let's Think in Two Steps: Mitigating Agreement Bias in MLLMs with Self-Grounded
Verification"**. Abstract quotes: *"a strong tendency for MLLMs to over-validate agent
behavior--a phenomenon we term agreement bias. This bias is pervasive, resilient to test-time
scaling"*; *"improving failure detection by 25pp and accuracy by 14pp"*;
*"surpassing the previous state of the art by 20pp"*. https://arxiv.org/abs/2507.11662

## What this changes in the project docs

- Cite **Skyvern PR #3080**, not #3099, and describe it as a frontend display decision.
- Describe UI-TARS #1952 as a settlement-stage failure.
- Do not claim browser-use "waits 0.5 s"; say 0.1 s between actions, 0.5 s declared.
- Name browser-use PR **#5145** as the in-repo generalization attempt and monitor it.
- Position against playwright-mcp precisely: it has network-drain settlement, no delta.
