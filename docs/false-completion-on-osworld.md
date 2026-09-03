# False completion on OSWorld: what the leaderboard cannot see

*2026-09-03. Every number below is recomputed from `data/track1_all_runs.csv` in the companion
repository, [JeremiahM37/osworld-false-completion](https://github.com/JeremiahM37/osworld-false-completion);
I re-ran that recount before writing this and it matches the original run to the row.*

## The claim

Across the 91 public OSWorld-Verified leaderboard runs whose released trajectories can be parsed,
34,437 scored task-runs on feasible tasks, **53.0 % (95 % Wilson interval [52.3, 53.6]) of the
task-runs the benchmark's validator scored as failed end with the agent claiming success** (11,061
of 20,888). Restricted to the failures where the agent stopped on its own instead of running out
of steps, it is 76.4 % [75.7, 77.1] (11,061 of 14,482). The rate is driven by the step budget:
28.9 % at 15 steps, 59.0 % at 50, 69.0 % at 100. None of this shows in the leaderboard number,
because OSWorld's `evaluate()` (`desktop_env/desktop_env.py`, lines 458-479 at commit
`fc31a9049664292fcb35d6e501ee1dc839f2cf6d` of `xlang-ai/OSWorld`, 2026-08-30) never reads the
agent's `DONE`.

## Why the leaderboard cannot see it

OSWorld gives an agent two special actions: `FAIL` ("this task is infeasible, I am stopping") and
`DONE` ("I have finished"). In `step()` both just set `done = True` and an `info` dict; neither
touches the VM. The asymmetry is in `evaluate()`:

```python
if self.evaluator['func'] == "infeasible":
    if len(self.action_history) > 0:
        last_action = self.action_history[-1]
        if last_action == "FAIL" or (type(last_action) == dict and last_action.get('action_type') == 'FAIL'):
            return 1
    return 0
else:
    if len(self.action_history) > 0:
        last_action = self.action_history[-1]
        if last_action == "FAIL" or (type(last_action) == dict and last_action.get('action_type') == 'FAIL'):
            return 0
# ... fall through to the metric functions, which inspect VM state
```

That is the only place the action history is consulted, and it tests only for `FAIL`. On a
feasible task a trajectory that ends in `DONE`, one that hits the step limit, and one that stops
on an ordinary click are scored identically, by the state of the VM. A false "done" is a plain
failure, and the success rate is exactly as correct as if the agent had said nothing. I confirmed
this live: a scripted agent that does nothing but emit `DONE` at step 2 scored 0.0 on three of
three feasible Chrome tasks against the real validator on a VM built from xlang's image.

The benchmark measures whether the task got done, not whether the agent knows; for anyone
deploying these agents the second matters, because a false "done" is what stops a human from
checking.

## Method

Only public data. xlang hosts every verified leaderboard trajectory in the Hugging Face dataset
`xlangai/ubuntu_osworld_verified_trajs` (MIT, 100 archives, 500.4 GB, last modified 2026-08-07).
Almost all of that is screenshots and recordings.
`hf_remote_zip.py` reads each zip's central directory from its tail over HTTP Range requests,
computes the exact byte range of every `traj.jsonl`, `result.txt` and small text member, fetches
those ranges and inflates them locally. No zip is ever downloaded; 2.3 GB was kept out of 500 GB,
with zero fetch errors across the 99 zips. The two archives that cannot be range-read (a `.tar.gz`,
and a zip whose only payload is a 4.48 GB `.tar.gz`) were streamed once through `tarfile`.

`parse_trajs.py` classifies each task-run's **terminal self-report** from the last recorded action:

- **DONE**: the string `DONE`, `{"action_type": "DONE"}`, `terminate(status='success')`,
  `Agent.exit(success=True)`, UI-TARS's `finished`, Holo3's last `agp_actions` entry, CoACT-1's
  orchestrator `TERMINATE`, or, for the Anthropic "shareable" exports, a final reply with no tool
  call (what that runner turns into `DONE`).
- **FAIL**: the corresponding failure forms, `infeasible`, CoACT-1's `INFEASIBLE`, `[INFEASIBLE]`
  in the Anthropic reply.
- **max-steps**: neither, at the run's step budget. **other**: neither, stopped before it.
  **crash**: an `{"Error": ...}` row or an empty trajectory.

The validator verdict is `result.txt`; pass means a score of at least 0.99 (a few metrics return
fractions). 27 of the 369 tasks in `evaluation_examples` use the `infeasible` evaluator; there the
*correct* terminal action is `FAIL`, so they are reported separately and kept out of the headline.
Budgets, leaderboard identities and the reason for every skipped folder are in `run_registry.json`.

As a check on identity, each run's all-task pass rate was compared with the success rate on its
leaderboard row: 86 of the 88 runs with a row agree within 3 pp, 87 within 5 pp. The one outside
is UI-TARS-250705 at 15 steps, for which the leaderboard has no 15-step row (the nearest is the
100-step row, 10.3 pp away). Three folders (Qwen3.5's internal eval, two AGIAgent runs) have no
leaderboard row and are pooled, labelled as such.

Coverage: 37,486 task-run rows from 91 folders; 282 rows with a `result.txt` but no released
trajectory excluded; 37,204 eligible, of which 34,437 feasible-task and 2,767 infeasible-task
rows. Nine of the 100 archives contribute nothing: three use the OSWorld 2.0 task set, one is a
byte-identical duplicate, one holds only nested internal fallback runs, three Maestro folders
released scores only, and VLAA-GUI's runner never writes `DONE`/`FAIL` to `traj.jsonl`.

## Results

Pooled, feasible tasks, all 91 runs:

| terminal self-report | validator pass | validator fail |
|---|---:|---:|
| DONE | 13,266 | 11,061 |
| FAIL | 80 | 3,421 |
| max-steps | 189 | 5,511 |
| crash | 1 | 7 |
| other | 13 | 888 |

- n scored = 34,437; validator pass rate 39.3 %
- **P(DONE | validator fail) = 11,061 / 20,888 = 53.0 %, Wilson 95 % [52.3, 53.6]**
- P(DONE | validator fail, voluntary stop) = 11,061 / 14,482 = 76.4 % [75.7, 77.1]
- P(FAIL action | validator fail) = 16.4 %; claim precision P(pass | DONE) = 54.5 % (24,327 DONE claims)

An honest `FAIL` on an uncompleted feasible task is the rare outcome, one failure in six; a `DONE`
claim is right about as often as a coin flip.

**By step budget.** At 15 steps half of all failures are timeouts; with room to finish, agents
finish, and claim success.

| budget | runs | n feas | pass % | max-steps share of fails | DONE&fail / fail | P(DONE\|fail) | Wilson 95 % | voluntary P(DONE\|fail) (n) |
|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 15 | 24 | 9,353 | 27.5 % | 50.6 % | 1,958 / 6,779 | **28.9 %** | [27.8, 30.0] | 67.1 % (2,916) |
| 50 | 26 | 10,015 | 37.7 % | 18.5 % | 3,681 / 6,240 | **59.0 %** | [57.8, 60.2] | 73.7 % (4,992) |
| 100 | 39 | 14,401 | 47.8 % | 12.3 % | 5,182 / 7,512 | **69.0 %** | [67.9, 70.0] | 83.3 % (6,222) |
| 30 | 1 | 334 | 35.9 % | 0.0 % | 115 / 214 | **53.7 %** | [47.1, 60.3] | 53.7 % (214) |

The same effect within a model (P(DONE | fail) on feasible tasks; n fail in parentheses):

| leaderboard model | 15 | 50 | 100 |
|---|---:|---:|---:|
| claude-3-7-sonnet-20250219 | 18.1 % (238) | 66.3 % (208) | 93.8 % (209) |
| claude-4-sonnet-20250514 | 17.5 % (223) | 70.6 % (180) | 94.7 % (190) |
| claude-sonnet-4-5-20250929 | 13.5 % (185) | 62.4 % (133) | 81.0 % (116) |
| opencua-7b | 42.1 % (748) | 82.6 % (708) | 93.6 % (729) |
| opencua-32b | 38.8 % (703) | 70.0 % (674) | 78.2 % (673) |
| agent s2.5 w/ o3 | 31.3 % (211) | 75.9 % (162) | 85.4 % (157) |
| Jedi-7B w/ o3 | 19.6 % (194) | 44.8 % (172) | 57.6 % (170) |
| uitars-1.5-7b (2 runs each) | 18.1 % (525) | 49.8 % (490) | 64.6 % (486) |
| o3 | 6.2 % (308) | 14.3 % (286) | 19.2 % (271) |

**By agent family.** Family is the runner or framework, assigned by me in `run_registry.json`:
*Claude* is the Anthropic computer-use runner, *Agent S* is Simular's S2/S2.5/S3 with any backbone,
and third-party frameworks that use a Claude or GPT model (HIPPO, Intelligence-Indeed, GBOX,
UiPath, OpenAPA, ...) are *other*.

| family | runs | n feas | pass % | DONE&fail / fail | P(DONE\|fail) | Wilson 95 % | voluntary P(DONE\|fail) (n) | P(FAIL action\|fail) | P(pass\|DONE) |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| Claude | 12 | 4,000 | 52.6 % | 1,088 / 1,896 | **57.4 %** | [55.1, 59.6] | 97.9 % (1,111) | 1.2 % | 65.0 % |
| UI-TARS | 13 | 4,331 | 29.9 % | 1,385 / 3,038 | **45.6 %** | [43.8, 47.4] | 50.7 % (2,734) | 44.4 % | 47.7 % |
| AutoGLM | 4 | 1,330 | 42.5 % | 313 / 765 | **40.9 %** | [37.5, 44.4] | 96.0 % (326) | 1.7 % | 63.7 % |
| Agent S | 7 | 2,378 | 52.1 % | 726 / 1,139 | **63.7 %** | [60.9, 66.5] | 92.0 % (789) | 5.5 % | 63.0 % |
| OpenCUA | 12 | 8,001 | 26.9 % | 4,023 / 5,849 | **68.8 %** | [67.6, 70.0] | 94.0 % (4,280) | 4.4 % | 34.4 % |
| Holo | 1 | 664 | 77.0 % | 144 / 153 | **94.1 %** | [89.2, 96.9] | 94.1 % (153) | 5.9 % | 77.9 % |
| other | 42 | 13,733 | 41.4 % | 3,382 / 8,048 | **42.0 %** | [40.9, 43.1] | 66.5 % (5,089) | 21.2 % | 62.3 % |

Every family is above 40 %. For the Anthropic runner the voluntary-stop column is the one to
quote: when it stops on its own on a task it has not completed, it says `DONE` 97.9 % of the time.

**Infeasible-evaluator tasks**, reported separately (27 tasks; `DONE` here is a missed
infeasibility, not a false completion; the validator passes iff the last action was `FAIL`):

| group | n | DONE (pass/fail) | FAIL (pass/fail) | max-steps (pass/fail) | crash/other (pass/fail) | P(DONE) | Wilson 95 % | P(FAIL) |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| ALL | 2,767 | 11 / 1,071 | 1,063 / 5 | 38 / 498 | 17 / 64 | 39.1 % | [37.3, 40.9] | 38.6 % |
| Claude | 324 | 4 / 164 | 69 / 0 | 0 / 87 | 0 / 0 | 51.9 % | [46.4, 57.2] | 21.3 % |
| UI-TARS | 347 | 0 / 202 | 120 / 0 | 0 / 25 | 0 / 0 | 58.2 % | [53.0, 63.3] | 34.6 % |
| AutoGLM | 108 | 0 / 10 | 43 / 0 | 38 / 0 | 17 / 0 | 9.3 % | [5.1, 16.2] | 39.8 % |
| Agent S | 189 | 0 / 87 | 77 / 0 | 0 / 25 | 0 / 0 | 46.0 % | [39.1, 53.1] | 40.7 % |
| OpenCUA | 631 | 0 / 306 | 137 / 0 | 0 / 188 | 0 / 0 | 48.5 % | [44.6, 52.4] | 21.7 % |
| Holo | 54 | 0 / 6 | 48 / 0 | 0 / 0 | 0 / 0 | 11.1 % | [5.2, 22.2] | 88.9 % |
| other | 1,114 | 7 / 296 | 569 / 5 | 0 / 173 | 0 / 64 | 27.2 % | [24.7, 29.9] | 51.5 % |

The 38 AutoGLM max-steps "passes" are the runner appending `FAIL` to the action history itself
when the loop ends; the agent never said it.

**The three runs under 10 %** are budget or runner artefacts, not honesty. `o3` at 15 steps is at
6.2 % (19 of 308) because 269 of its 308 failures are timeouts; when it does stop on its own it says
`DONE` 19 times out of 39. `qwen2.5-vl-72b-instruct` at 100 and 15 steps is at 3.2 % (10 of 316) and
3.8 % (12 of 318) because that runner logs only successfully parsed actions: 195 and 155 of its
failures end as `other` (the trajectory just stops), most of the rest hit the budget, and it never
emitted `FAIL`, so every voluntary stop was a `DONE`. The full per-run table (all 91
rows, intervals, leaderboard cross-check) is `results/track1_confusion.md` in the companion
repository.

## Prior work

The same quantity has been measured before, on single pipelines, and those numbers are consistent
with these.

- **CURA: Certified Runtime Alarms for Computer-Use Agents**, [arXiv 2608.27808](https://arxiv.org/abs/2608.27808)
  (Kumar, Tayebati, Naik, Rios, Ahuja, Tickoo, Krishnan, Trivedi; submitted 2026-08-28). A
  three-stage qwen3.7-plus pipeline on the 361 OSWorld-Verified tasks at 100 steps: 290 solved, 71
  failed; 64 of the 71 failures (90 %) terminate with a claim of success, and the executor's
  explicit fail affordance is invoked zero times in roughly 9,100 calls. Their Table 9 gives
  UI-TARS-1.5-7B 214 completion claims of which 118 were false, plus 142 self-declared failures:
  by my arithmetic P(DONE | fail) = 118 / 248 = 47.6 %. Live on VMs, no trajectories released, no
  discussion of the `evaluate()` asymmetry. The public qwen3.7-plus run here gives 80.2 %
  [70.6, 87.3] at 100 steps; the six uitars-1.5-7b runs pool to 64.6 % at 100 and 49.8 % at 50.
- **From Confident Closing to Silent Failure: Characterizing False Success in LLM Agents**,
  [arXiv 2606.09863](https://arxiv.org/abs/2606.09863) (Advani, 2026-06-01). The same quantity,
  called "false success", on tau2-bench (45-48 % of failures) and AppWorld (75.8 %), not OSWorld;
  finds that LLM judges detect it poorly (AUROC at most 0.65).

Both links resolved on 2026-09-03. What this adds is the rate across the published leaderboard
rather than one pipeline (91 runs, per run, family and budget, each with an interval, from
trajectories anyone can re-read) and the source observation explaining why the leaderboard cannot
show it.

## Limitations

- **Runner artefacts are inside the numbers.** UI-TARS's runner turns a client or parse error into
  `DONE` (`mm_agents/uitars_agent.py:662-704`); OpenCUA's and the Anthropic runner turn them into
  `FAIL`; AutoGLM's appends `FAIL` at the step limit; the qwen2.5-vl runner drops unparsed steps.
  The Anthropic "shareable" exports (the fable-5 and opus-5 runs) carry no explicit `DONE` at all:
  a final reply with no tool call is taken as `DONE`, which is what that runner does but is an
  inference on my side. These move individual runs by many points in both directions, and the
  public data cannot separate a model's self-report from its runner's fallback. I do not correct
  for them.
- **Unreleased trajectories.** 34 of the 144 numeric leaderboard rows (16 model names) have none in
  the release, including OpenAI's computer-use-preview, opencua-72b-preview, both Pointer Agent
  rows, Coasty, OS-Symphony and both UiPath/Opus 4.5 rows, so the top of the current leaderboard
  is under-represented.
- **OSWorld-Verified Ubuntu only.** No Windows, no OSWorld 2.0, no other benchmark. The validator
  is itself noisy in both directions, so "validator fail" is not ground truth.
- **Identity by pass rate; family labels are mine.** One UI-TARS folder
  (`results_omnitars_100_steps`) is ambiguous between two same-era rows; three folders have no row.
- **Pooling weights runs equally by task-run**, so the 12 OpenCUA runs (8,001 feasible rows, three
  rollouts each) carry 23 % of the pool, and one run is a best-of-10 selection. The per-family,
  per-budget and per-run tables are the honest view; the pooled number is a summary of them, not a
  population estimate.

## What to do about it

The receipt this repository implements is one answer at the level of a single action: after each
UI action it settles the page and returns a verdict (`ok`, `no_op`, `blocked`, `navigated`,
`unknown`) with the evidence, so an agent, or a gate around it, has something other than its own
belief to check before saying done. What that does to false completions on a small controlled
bench, denominators included, is in the [numbers section of the README](../README.md#the-numbers).

## Reproduce

Everything is in [JeremiahM37/osworld-false-completion](https://github.com/JeremiahM37/osworld-false-completion).
`data/track1_all_runs.csv` has one row per task-run with the zip URL and in-zip directory it came
from, so the headline takes seconds and no download:

```bash
git clone https://github.com/JeremiahM37/osworld-false-completion
cd osworld-false-completion
python3 -m pip install -r requirements.txt
make recount          # pooled matrix, P(DONE|fail) with Wilson 95 %, per budget, per family
make report           # rebuild results/track1_confusion.{md,json} from the CSV (byte-identical)
make test             # confusion.py on synthetic rows + the CSV reproduces every number above
```

To re-derive the CSV from the public release (about 2.3 GB of range reads; a read-scope Hugging
Face token in `HF_TOKEN` avoids 429s):

```bash
export HF_TOKEN=hf_...
make fetch            # range-read traj.jsonl / result.txt out of the 100 archives into raw/
make parse            # classify terminal self-reports -> rows_public.jsonl, then the report
```

`data/run_registry.json` records every folder's family, leaderboard identity, budget and, for the
excluded archives, the reason; feasibility uses the vendored `data/infeasible_tasks.json` unless
`OSWORLD_DIR` points at an OSWorld checkout.
