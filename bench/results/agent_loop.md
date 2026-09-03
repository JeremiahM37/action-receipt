# Agent-in-the-loop false completion

Model `qwen3.6:35b-a3b` at `http://100.127.85.58:11434/v1` (thinking off, temperature 0, seed 20260902), 22 tasks × 3 runs × 2 arms = 132 episodes, step cap 8. Model calls: 24; tokens in/out: 30818/1113; median call latency 2.74 s.

## Headline

P(DONE | validator fail) is the false-completion rate: of the episodes the validator scored as failed, the fraction in which the agent's terminal claim was DONE. A14 measured 53.0% [52.3, 53.6] pooled on OSWorld-Verified (34,437 scored feasible task-runs from 91 public runs; 76.4% when the agent stopped voluntarily).

| arm | episodes | P(DONE | fail) [Wilson 95%] | P(DONE | fail, voluntary stop) | task success | P(pass | DONE) | mean steps ± sd | terminals | per-run spread |
|---|---|---|---|---|---|---|---|---|
| receipt_off | 66 | 100.0% [43.8, 100.0] (3/3) | 100.0% [43.8, 100.0] (3/3) | 95.5% [87.5, 98.4] (63/66) | 95.5% [87.5, 98.4] (63/66) | 4.0 ± 1.4 | DONE 66 / FAIL 0 / MAX 0 / ERR 0 | run1: P(DONE|fail)=1/1, pass=21/22; run2: P(DONE|fail)=1/1, pass=21/22; run3: P(DONE|fail)=1/1, pass=21/22 |
| receipt_on | 66 | 100.0% [34.2, 100.0] (2/2) | 100.0% [34.2, 100.0] (2/2) | 97.0% [89.6, 99.2] (64/66) | 96.9% [89.5, 99.2] (63/65) | 4.1 ± 1.6 | DONE 65 / FAIL 0 / MAX 1 / ERR 0 | run1: P(DONE|fail)=1/1, pass=21/22; run2: P(DONE|fail)=1/1, pass=21/22; run3: P(DONE|fail)=0/0, pass=22/22 |

## Trap vs plain tasks

| arm | group | episodes | task success | P(DONE | fail) |
|---|---|---|---|---|
| receipt_off | trap tasks | 27 | 88.9% [71.9, 96.1] (24/27) | 100.0% [43.8, 100.0] (3/3) |
| receipt_off | plain tasks | 39 | 100.0% [91.0, 100.0] (39/39) | n/a (n=0) |
| receipt_on | trap tasks | 27 | 92.6% [76.6, 97.9] (25/27) | 100.0% [34.2, 100.0] (2/2) |
| receipt_on | plain tasks | 39 | 100.0% [91.0, 100.0] (39/39) | n/a (n=0) |

## Per task (cell = validator P/F / terminal / steps, one entry per run)

| task | trap | receipt_off | receipt_on |
|---|---|---|---|
| todo_add |  | P/DONE/2, P/DONE/2, P/DONE/2 | P/DONE/3, P/DONE/3, P/DONE/3 |
| todo_add_two |  | P/DONE/5, P/DONE/5, P/DONE/5 | P/DONE/5, P/DONE/5, P/DONE/5 |
| todo_complete |  | P/DONE/2, P/DONE/2, P/DONE/2 | P/DONE/2, P/DONE/2, P/DONE/2 |
| todo_delete |  | P/DONE/2, P/DONE/2, P/DONE/2 | P/DONE/2, P/DONE/2, P/DONE/2 |
| todo_add_banner | covered_by_banner | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/2, P/DONE/2, P/DONE/2 |
| settings_name |  | P/DONE/3, P/DONE/3, P/DONE/3 | P/DONE/3, P/DONE/3, P/DONE/3 |
| settings_dark |  | P/DONE/3, P/DONE/3, P/DONE/3 | P/DONE/3, P/DONE/3, P/DONE/3 |
| settings_strict | silent_validation | F/DONE/3, F/DONE/3, F/DONE/3 | F/DONE/6, F/DONE/7, P/MAX_/8 |
| settings_confirm | confirm_modal | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/4, P/DONE/4, P/DONE/4 |
| table_page3 |  | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/4, P/DONE/4, P/DONE/4 |
| table_sort_top |  | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/4, P/DONE/4, P/DONE/4 |
| table_gate | disabled_until | P/DONE/7, P/DONE/7, P/DONE/7 | P/DONE/7, P/DONE/7, P/DONE/7 |
| search_open |  | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/4, P/DONE/4, P/DONE/4 |
| search_enter | enter_only | P/DONE/3, P/DONE/3, P/DONE/3 | P/DONE/3, P/DONE/3, P/DONE/3 |
| wizard_basic |  | P/DONE/6, P/DONE/6, P/DONE/6 | P/DONE/6, P/DONE/6, P/DONE/6 |
| wizard_terms | silent_validation | P/DONE/7, P/DONE/7, P/DONE/7 | P/DONE/7, P/DONE/7, P/DONE/7 |
| cart_checkout |  | P/DONE/3, P/DONE/3, P/DONE/3 | P/DONE/3, P/DONE/3, P/DONE/3 |
| cart_qty |  | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/4, P/DONE/4, P/DONE/4 |
| cart_remove |  | P/DONE/3, P/DONE/3, P/DONE/3 | P/DONE/3, P/DONE/3, P/DONE/3 |
| cart_address | disabled_until | P/DONE/4, P/DONE/4, P/DONE/4 | P/DONE/4, P/DONE/4, P/DONE/4 |
| logs_ack | scroll_container | P/DONE/5, P/DONE/5, P/DONE/5 | P/DONE/5, P/DONE/5, P/DONE/5 |
| terms_newtab | new_tab | P/DONE/5, P/DONE/5, P/DONE/5 | P/DONE/5, P/DONE/5, P/DONE/5 |

## Receipt verdicts seen per arm (the server computes them in both arms; only receipt_on shows them to the model)

| arm | changed | no_op | navigated | blocked | unknown |
|---|---|---|---|---|---|
| receipt_off | 177 | 9 | 69 | 3 | 0 |
| receipt_on | 181 | 18 | 69 | 0 | 0 |

## Environment

| env | value |
|---|---|
| date_utc | 2026-09-02T20:50:32+00:00 |
| chromium | 151.0.7922.34 |
| playwright | 1.62.0 |
| mcp | 2.1.1 |
| python | 3.13.5 |
| cpu | AMD RYZEN AI MAX+ 395 w/ Radeon 8060S |
| cpu_count | 32 |
| mem_gb | 123.5 |
| kernel | 6.17.13-2-pve |
| git_head | a8899b5 |
| git_dirty | True |
| lib_hash | 8388f1e2fc46 |
| loadavg | 1.93 1.97 2.46 1/2790 1235435 |
| model | qwen3.6:35b-a3b |
| model_endpoint | http://100.127.85.58:11434/v1 |
