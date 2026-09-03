# Using action-receipt beside playwright-mcp

If an agent already drives the browser with [`@playwright/mcp`](https://github.com/microsoft/playwright-mcp),
it can keep doing so and get a receipt for every action anyway. The two servers share one
Chromium over CDP; playwright-mcp performs the action, action-receipt observes it in **wrap
mode** (`receipt_begin` → the playwright-mcp call → `receipt_end`) and returns the same receipt
its own tools would: verdict, evidence, hint, settlement, delta.

Verified with `@playwright/mcp@0.0.80` (Node 20, `npx`), which supports `--cdp-endpoint <url>`
(`npx @playwright/mcp@0.0.80 --help`); the e2e test `tests/e2e/test_playwright_mcp.py` runs
exactly the flow below and is pinned to that version.

## How the browser is shared

action-receipt launches the Chromium and publishes its remote-debugging port; playwright-mcp
attaches to it. That order matters for two reasons: action-receipt's observer script must be in
the browser context before pages load, and the browser must exist before playwright-mcp's first
call, which may come before ours — so with `--cdp-listen` the browser is started with the server,
not on the first tool call.

```
action-receipt --cdp-listen 9222      launches Chromium with --remote-debugging-port=9222 and works
                                      in its default context; browser_info() reports the endpoint
@playwright/mcp --cdp-endpoint http://127.0.0.1:9222
                                      attaches to the same browser; non-isolated, so it uses the
                                      same default context and the same tabs
```

Both servers see the same tabs. playwright-mcp's `browser_navigate` opens a tab action-receipt's
`tabs` lists; action-receipt's `open` opens one playwright-mcp's `browser_tabs` lists.

The alternative — a Chromium you launch yourself with `--remote-debugging-port=9222`, both servers
attached with `--cdp` / `--cdp-endpoint` — works the same way and is what the e2e test does. Use
`--cdp` on action-receipt then; `--cdp` and `--cdp-listen` are exclusive.

## MCP configuration

Claude Code (`~/.claude.json`, or `claude mcp add-json <name> '<block>'`):

```json
{
  "mcpServers": {
    "action-receipt": {
      "command": "/home/you/.venvs/action-receipt/bin/action-receipt",
      "args": ["--cdp-listen", "9222"]
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@0.0.80", "--cdp-endpoint", "http://127.0.0.1:9222"]
    }
  }
}
```

Claude Desktop (`claude_desktop_config.json`) takes the same block. Add `"--enforce-done"` to
action-receipt's args to make `done` a real gate (see *Enforcing the receipt* in the README), and
`"--headed"` to watch the browser. `examples/playwright-mcp/mcp.json` is this file, ready to copy.

Startup order is not guaranteed by either client; that is what `--cdp-listen` absorbs. If
playwright-mcp still starts first and its first call fails with `connect ECONNREFUSED`, retry —
action-receipt takes about a second to bring the browser up. playwright-mcp's `--cdp-timeout`
(default 30 s) only applies to its own connect, so the retry is the agent's.

## The agent-side protocol

Every playwright-mcp action that should have a receipt is bracketed:

1. `receipt_begin(label="browser_click", selector="#save", page_url="orders")` — the before
   capture. `selector` (a Playwright selector for the element about to be acted on) is what turns
   a bare diff into a pre-flight: disabled / covered / readonly / offscreen are checked and named.
   `page_url` picks the tab to observe by substring when more than one is open.
2. The playwright-mcp call — `browser_click`, `browser_type`, `browser_select_option`,
   `browser_press_key`, `browser_navigate`, … — exactly as before.
3. `receipt_end()` — settle, capture, diff, decide. The result is `{"receipt": {...}}` with
   `verdict`, `evidence`, `hint` and the rest.

The labels are free text; using the playwright-mcp tool name (`browser_click`) keeps a trace
readable. A `receipt_end` without a `receipt_begin` is a structured error, not a crash; a
`receipt_begin` followed by no action yields an honest `no_op`.

Put this in the agent's instructions, not in its judgement:

> Before every playwright browser action call `receipt_begin` with the target's selector; after
> it call `receipt_end` and read `receipt.verdict` before planning the next step. `no_op` and
> `blocked` mean the action did **not** take effect — follow `receipt.hint` instead of repeating
> the action. Call `done` when the task is complete.

A worked example of the whole loop, driven from Python over stdio, is
`examples/playwright-mcp/drive_both.py`.

## What the receipt sees, and what it cannot

- **The window is `receipt_begin` → `receipt_end`.** Anything playwright-mcp does *before* the
  begin call (its own navigation, an implicit scroll-into-view) is not in the receipt. Call
  `receipt_begin` right before the action, not once per task.
- **playwright-mcp's own settle is a blind 500 ms** (`--timeout-settle`); the receipt's settlement
  is measured and independent of it. If playwright-mcp returns before the page has finished,
  `receipt_end` still waits for quiet, so the receipt is not stale — but the agent's *snapshot*
  from playwright-mcp may be.
- **Two clients on one CDP browser.** Both attach to the default context; either can open or
  close tabs and the other sees it. Do not add `--isolated` to playwright-mcp: it would create
  its own context and action-receipt's wrap mode could not observe its tabs.
- **Closed shadow roots** are walked only on pages that loaded *after* action-receipt attached
  (the observer is an init script); on a page that was already open only open roots are found.
- **The receipt is per action.** Whether the *task* is done is not something it answers; the
  `done` gate only refuses a claim made right after an action that had no effect.
