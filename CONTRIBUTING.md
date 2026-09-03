# Contributing

Thanks for looking. The project is small and deliberately narrow: a receipt is a pure function
of the live page, and every claim in the docs is backed by a test or a bench number. Changes
should keep both true.

## Setting up

```bash
git clone https://github.com/JeremiahM37/action-receipt && cd action-receipt
python3 -m venv .venv && . .venv/bin/activate
make install            # pip install -e '.[dev]' + playwright install chromium
pre-commit install      # ruff + ruff-format + whitespace hooks on commit
```

Python ≥ 3.11. `make help` lists every target; each one is exactly what the matching CI job runs.

## Test tiers

The suite is split into three tiers by directory; the marker is assigned from the directory
(`tests/conftest.py`), so `-m <tier>` always selects a whole tier. `tests/README.md` has the
detail; the short version:

| tier | where | needs | runtime | run |
|---|---|---|---|---|
| **unit** | `tests/unit/` | nothing (no browser) | < 5 s | `make test-unit` |
| **integration** | `tests/integration/` | Chromium, local fixture pages | ~2 min | `make test-integration` |
| **e2e** | `tests/e2e/` | Chromium + the real MCP server over stdio | ~1 min | `make test-e2e` |

`make test` runs all three; `make coverage` runs unit + integration under `pytest-cov`.
One test, browser visible, for debugging:

```bash
AR_HEADED=1 python -m pytest tests/integration/test_receipt.py -k covered -s
```

(`AR_HEADED=1` makes the `session` fixture launch a headed Chromium; the e2e tier always runs
its own headless Chromium because the server is a subprocess.)

Every receipt a test produces is validated against the strict schema at fixture teardown, so a
schema regression fails whichever test produced it - you do not need to add validation calls.

## Adding a fixture page

Integration fixtures live in `tests/fixtures/` and are served by the shared fixture server
(`tests/conftest.py::base_url`) at `http://127.0.0.1:<port>/<name>.html`. The server also has
`/slow?ms=N`, `/items?from=&n=&ms=`, `/submit` (POST → 400), `/nolen`, `/file.bin` and
`/fail?status=S&ms=N` for pages that need a network side. Keep a fixture to the one behaviour
it exists to show, name that behaviour in a comment at the top of the file, and add the case to
the table in `docs/TESTING.md`. Query parameters (`?mode=…`) are the way to get variants out
of one file; see `tests/fixtures/bg.html`.

Bench apps (`bench/fixtures/apps/`) are served to the e2e tier under `/bench/apps/…`; they expose
`window.__state()` so a validator can read the outcome without parsing the DOM.

## Adding a verdict evidence token

Evidence strings are the receipt's vocabulary; agents key on their prefix. To add one:

1. Emit it in `action_receipt/verdict.py` (`_collect_evidence` for delta-derived items, `decide`
   for rule-derived ones). The prefix before the first `:` is the token.
2. If it needs a new delta field, add it to `Delta` (`delta.py`) **and** to `DeltaModel`
   (`schema.py`) - the schema is `extra="forbid"`, so an unlisted field fails every test. Add the
   invariant that ties the field to the evidence in `ReceiptModel._invariants` when one exists
   (see `validation_blocked` for the pattern).
3. Add the token to `EVIDENCE_TOKENS` in `tests/unit/test_verdict.py` and a decision-table case
   that produces it; the coverage test fails until the table can emit it.
4. Document it in the README's verdict table and in `docs/DESIGN.md` §4.

A new `settled_by` value goes in `settle._SETTLED_BY_PRIORITY` (its position *is* the tie-break
order) and in `schema.SettledBy`; `tests/unit/test_settle.py` checks the two agree.

## Reproducing the benchmarks

`bench/REPORT.md` is assembled from `bench/results/*.json|md`, which are committed. To reproduce:

```bash
make bench-smoke                 # <= 2 min subset -> bench/results/smoke/ (never the published set)
bench/run_all.sh                 # the full suite (~hours; bench 4 needs an OpenAI-compatible endpoint)
bench/run_all.sh --live          # + one pass over five live public pages
```

Every result file records the environment and `lib_hash` (SHA-1 of `action_receipt/*.py`) it
measured, so a number is never quoted without the source it came from. `python -m bench.report`
regenerates `REPORT.md` from the result files; do not type numbers into it by hand.

## Style

`make lint` = `ruff check` + `ruff format --check` + `mypy` (config in `pyproject.toml`; line
length 110). Commit messages state the problem and the decision, not the tool that made it.
Keep the docs' voice: numbers with their source, limits stated next to the claim they bound.

## Pull requests

Open an issue first for anything beyond a fix, so the design question is settled before the
code. CI must be green on `lint`, `unit`, `integration` and `e2e`; `bench-smoke` and CodeQL are
informational.
