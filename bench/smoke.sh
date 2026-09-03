#!/usr/bin/env bash
# The <= 2-minute bench subset (also the CI `bench-smoke` job). Local fixtures only: no Ollama,
# no live sites. Results go to $AR_BENCH_RESULTS (default bench/results/smoke/) so the published
# bench/results/*.md are never touched by a smoke run.
#
#   make bench-smoke              # or: AR_BENCH_RESULTS=bench/results/smoke bench/smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python}
if [ -z "${PY_SET:-}" ] && [ -x "$HOME/.venvs/action-receipt/bin/python" ] && [ "$PY" = python ]; then
  PY="$HOME/.venvs/action-receipt/bin/python"
fi
export AR_BENCH_RESULTS=${AR_BENCH_RESULTS:-bench/results/smoke}
mkdir -p "$AR_BENCH_RESULTS"
LOG="$AR_BENCH_RESULTS/logs"; mkdir -p "$LOG"
ACC_CASES=${AR_SMOKE_ACCURACY_CASES:-c01,c02,c03,c05,c14,c42,k01,s01,r01,r02}

echo "== smoke 1/3 overhead (n=5, warmup 1)"
$PY -m bench.overhead --n 5 --warmup 1 > "$LOG/overhead.log" 2>&1
echo "== smoke 2/3 settlement (2 trials, delays 100,500)"
$PY -m bench.settlement --trials 2 --delays 100,500 > "$LOG/settlement.log" 2>&1
echo "== smoke 3/3 accuracy (10 cases, 1 repeat)"
$PY -m bench.accuracy --repeats 1 --only "$ACC_CASES" > "$LOG/accuracy.log" 2>&1
echo "done: $(ls "$AR_BENCH_RESULTS"/*.md | tr '\n' ' ')"
