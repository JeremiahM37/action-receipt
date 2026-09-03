#!/usr/bin/env bash
# Run the whole action-receipt benchmark suite. Results land in bench/results/ (JSON + markdown
# per bench); bench/REPORT.md is assembled by hand from those files.
#
#   bench/run_all.sh            # benches 1-4 + 4 v2 (local fixtures + the local Ollama agent)
#   bench/run_all.sh --live     # also bench 5 (one pass over five live public pages)
#
# Environment: ~/.venvs/action-receipt (Playwright 1.62 / Chromium 151, mcp 2.1). The agent bench
# needs the OpenAI-compatible endpoint (default LXC 102 Ollama, qwen3.6:35b-a3b) reachable.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/.venvs/action-receipt/bin/python}
OUT=${AR_BENCH_LOG_DIR:-bench/results/logs}
mkdir -p "$OUT"

if [ ! -f bench/fixtures/real/wikipedia.html ]; then
  echo "== fetching real-page fixtures (once)"; $PY -m bench.fetch_fixtures > "$OUT/fetch_fixtures.log" 2>&1
fi

echo "== 1/5 overhead";    $PY -m bench.overhead   --n 50 --warmup 3          > "$OUT/overhead.log"   2>&1
echo "== 2/5 settlement";  $PY -m bench.settlement --trials 20                > "$OUT/settlement.log" 2>&1
echo "== 3/5 accuracy";    $PY -m bench.accuracy   --repeats 3                > "$OUT/accuracy.log"   2>&1
                          $PY -m bench.accuracy   --repeats 3 --prescroll    > "$OUT/accuracy_prescroll.log" 2>&1
echo "== 4/5 agent loop";  $PY -m bench.agent_loop --runs 3 --max-steps 8 --seed 20260902 --max-calls 900 --resume > "$OUT/agent_loop.log" 2>&1
echo "== 4b/5 agent loop v2 (hard set, 3 arms, 2 models concurrently)"
$PY -m bench.tasks.selftest > "$OUT/tasks_selftest.log" 2>&1
$PY -m bench.agent_loop_v2 --models qwen3.6:35b-a3b --set hard --max-calls 1300 --resume > "$OUT/agent_loop_v2_35b.log" 2>&1 &
$PY -m bench.agent_loop_v2 --models qwen3.5:4b      --set hard --max-calls 1300 --resume > "$OUT/agent_loop_v2_4b.log"  2>&1 &
wait
$PY -m bench.agent_loop_v2 --summarize-only > "$OUT/agent_loop_v2_summary.log" 2>&1
if [ "${1:-}" = "--live" ]; then
  echo "== 5/5 real-site smoke (live)"; $PY -m bench.realsite --live          > "$OUT/realsite.log"   2>&1
else
  echo "== 5/5 real-site smoke skipped (pass --live)"
fi
$PY -m bench.report > "$OUT/report.log" 2>&1
echo "done; see bench/REPORT.md and bench/results/*.md"
