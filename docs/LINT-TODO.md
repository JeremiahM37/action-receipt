# Lint / type findings deferred for `action_receipt/`

`action_receipt/` was frozen while the F10 fix landed, so the lint pass (ruff config in
`pyproject.toml`, line length 110; mypy on `action_receipt` only) was applied to `tests/` and
`bench/` but **not** to the library. Until the items below are applied, `make lint` (and the CI
`lint` job) is red on the library alone; every test tier is green.

None of these changes behaviour. Every one was applied to a scratch copy of the library and
verified there: `ruff check` clean, `ruff format --check` clean, `mypy` "no issues found in 9
source files", and the unit (160) and e2e (6) tiers green against the fixed copy.

## Apply in three commands

```bash
git apply docs/lint-todo.patch                     # the hand edits below (small; see the patch)
ruff check --fix action_receipt && ruff format action_receipt   # imports, __all__, deprecated typing import, the `a; b` lines
make lint                                          # expect: ruff clean, format clean, mypy clean
```

The patch was cut against the tree as of 2026-09-03 (after the F10 change to `verdict.py`,
`delta.py`, `schema.py`, `capture.py`, `inject.py`). If a hunk no longer applies, the table below
says what each hunk does so it can be redone by hand in a minute.

## Hand edits (in the patch)

| file:line | finding | change |
|---|---|---|
| `schema.py:22-23` | mypy `attr-defined`: `Literal.__args__` is not a typed attribute | `from typing import get_args`; `set(get_args(VerdictName))`, `set(get_args(SettledBy))` |
| `verdict.py:213` (`_dispatcher_scrolled`) | mypy `union-attr`: `bool(x) and x.lower()` does not narrow `str \| None` | `dispatch_error is not None and any(...)` |
| `verdict.py:236` (`substantive_change`) | ruff SIM103 needless bool | `return _visual_is_perceptual(d) and (...)` |
| `session.py:22, 96` | mypy `assignment` / `attr-defined`: `self._pw = None` is typed as `None`, so `.chromium` is an error | import `Playwright`; `self._pw: Playwright \| None = None` |
| `session.py:148` (`_track`) | mypy `arg-type`: `self.context` is `BrowserContext \| None` | `assert self.context is not None, "start() or attach_context() first"` before `PageState(...)` |
| `session.py:151` (`_track`) | mypy `call-overload`: the `close` handler lambda returns `PageState \| None`, Playwright wants `-> None` | a local `def _forget(p: Page) -> None: self._pages.pop(p, None)` registered with `page.on("close", _forget)` |
| `session.py:155` (`new_page`) | mypy `union-attr` on `self.context.new_page()` | same assert as above |
| `server.py:65` | ruff RUF100: `# noqa: BLE001` for a rule that is not enabled | drop the directive, keep the comment |
| `server.py:210` (`receipt_begin`) | mypy `union-attr`: `s.current` may be `None` | `"observing": s.current.url if s.current else None` |
| `settle.py:220, 287` (`NavTracker._on_dialog`) | ruff RUF006: the `asyncio.ensure_future(_handle())` task is not referenced and can be garbage-collected mid-flight | keep it in `self._tasks: set[asyncio.Future[None]]` and discard on completion (the standard pattern from the asyncio docs) |
| `inject.py:344, 420` | ruff UP031: `%d` formatting of the JS helper source | `const MAXP = __MAXP__;` and `.replace("__MAXP__", str(PATH_MAX_PARTS))` - `str.format` is not an option, the JS is full of braces |

## Auto-fixed by `ruff check --fix` + `ruff format`

- `I001` import sorting in `__init__.py` and `session.py`; `RUF022` sorts `__all__`.
- `UP035`: `from typing import Awaitable, Callable` → `from collections.abc import Awaitable, Callable` (`session.py:20`).
- `E702` (six `busy.append("dom"); last_busy["dom"] = now`-style lines in `settle.py:459-482`): the
  formatter splits them onto two lines; nothing else changes.
- `ruff format` reformats all nine files (mostly line wrapping and quote normalisation).
  Run it once, in its own commit, so the diff that matters stays readable.

## Refactor suggestions (not applied; from writing the unit tier)

These are places where a pure function would have made a unit test possible; each is covered by
an integration test today, so there is no coverage gap - only a speed/locality one.

1. **`settle.settle()` tie-break.** `settled_by = min(tied, key=lambda k: _SETTLED_BY_PRIORITY.index(k) …)`
   is the only piece of the priority rule, and it is inline in a 150-line coroutine. Lifting it to
   `def pick_settled_by(last_busy: dict[str, float]) -> str` would let the unit tier assert the
   exact rule (`tests/unit/test_settle.py::test_tie_break_picks_the_highest_priority_signal`
   currently re-states it instead of calling it).
2. **`settle.settle()` signal aggregation.** The `signals` dict assembled at the end (the
   `busy_until_ms` arithmetic, the `timers.long` cap) could be `build_signals(counters,
   last_busy, seen_busy_reason, net, cfg, t0, t_dispatch)`; the schema invariant
   "nothing in `long` is ≤ `timer_wait_ms`" is then testable without a page.
3. **`session.ReceiptSession.run()`.** The `password` masking of `args["text"]` and the
   `dispatch` dict construction are pure and could be small helpers; the unit tier covers
   `Receipt.to_dict()` but not the masking, which only `tests/integration/test_receipt.py::test_password_value_is_masked_in_receipt` exercises.
4. **`capture.capture_state()`** parses the tab-separated node blob inline
   (`for line in blob.split("\n"): …`). A `parse_nodes(blob) -> (nodes, background_paths, dom_hash)`
   would let the unit tier pin the wire format the JS emits (`path\tsig\tflag`), which today is
   only checked indirectly through fingerprint diffs.

None of these is worth doing before the release; they are noted so the unit tier can grow
without a browser when someone next touches those functions.
