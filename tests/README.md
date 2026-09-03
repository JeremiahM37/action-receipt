# Tests

Three tiers, selected by directory and by marker (assigned automatically from the directory in
`conftest.py`, so `-m` and a path give the same set).

| tier | marker | directory | what it needs | wall time | what it proves |
|---|---|---|---|---|---|
| unit | `unit` | `tests/unit/` | nothing - no browser, no network | **< 5 s** | the decision table, the diff, the schema invariants, settlement bookkeeping, the CLI - on synthetic inputs |
| integration | `integration` | `tests/integration/` | a Chromium (`playwright install chromium`) | **~2 min** (~110 s on a 32-core box, sequential) | receipt behaviour per action against the fixture pages in `tests/fixtures/`; robustness (CDP attach, crashed/closed tabs); every bench finding pinned as a regression test |
| e2e | `e2e` | `tests/e2e/` | a Chromium; the server is launched as a subprocess | **~1.5 min** | the real MCP stdio server end to end: the tool round trip, a scripted agent recovering from `no_op` / `blocked` / `navigated` receipts on the bench apps, CDP-attach isolation, wrap mode around a plain Playwright script, the `done` gate, `--cdp-listen`, and `@playwright/mcp` itself driving the shared browser |

`slow` marks tests that need something outside the repo — the local Ollama endpoint, or `npx`
and the npm registry (`test_playwright_mcp.py`, pinned to `@playwright/mcp@0.0.80`). Such a test
must skip itself, with the reason, when what it needs is unavailable.

## Running

```bash
make test-unit                       # or: pytest -m unit tests/unit
make test-integration                # or: pytest -m integration tests
make test-e2e                        # or: pytest -m e2e tests
make test                            # everything
make coverage                        # unit + integration with pytest-cov (terminal + coverage.xml)
pytest -m "not e2e" tests            # markers compose
```

Runs are sequential by design - settlement measures wall-clock quiet windows, and a loaded box
skews them. `-p no:cacheprovider` (what the Makefile passes) keeps `.pytest_cache` out of the tree.

### One test, browser visible

```bash
AR_HEADED=1 python -m pytest tests/integration/test_receipt.py::test_click_covered_button_is_blocked_with_cover_named -s
```

`AR_HEADED=1` makes the `session` fixture launch a headed Chromium and adds `slow_mo=250`.
Print the receipt from inside a test with `print(json.dumps(r.to_dict(), indent=1))` (`-s` shows
it). The e2e server subprocess is always headless; to watch one of those flows, run the same
tool calls against `action-receipt --headed` from an MCP client.

### Debugging a settlement question

`r.settlement.to_dict()` says which signal was last busy and for how long; `r.timing`
breaks the receipt into `pre_settle / capture_before / dispatch / settle / capture_after`. A
receipt that timed out lists `busy_at_timeout`.

## Layout

```
tests/
  conftest.py            fixture server (tests/fixtures + bench/fixtures under /bench/), the
                         `session` fixture, schema validation at teardown, tier markers
  fixtures/              the integration fixture pages (one behaviour each)
  unit/                  _synth.py builds captures / deltas / receipts without a browser
  integration/           test_receipt, test_cases, test_robustness, test_schema, test_bench_fixes, test_f10_validation
  e2e/                   conftest (MCP client helpers, own_chromium), test_mcp, test_agent_tasks,
                         test_cdp_attach, test_wrap_mode, test_enforce_done, test_cdp_listen,
                         test_playwright_mcp
```

`docs/TESTING.md` lists every integration case with what it proved or fixed and the measured
bounds the suite documents.
