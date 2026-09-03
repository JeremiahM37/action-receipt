# Overhead: receipt vs raw Playwright

n = 50 timed actions per cell after 3 warm-up actions; wall time in ms (p50 / p90). Every non-local request is aborted; the real pages are static copies (`bench/fixtures/real/SOURCES.md`).

| page | action | n | raw p50 / p90 | receipt p50 / p90 | receipt (no screenshot) p50 / p90 | receipt − raw (p50) |
|---|---|---|---|---|---|---|
| fixture: buttons.html | click | 50 | 33.1 / 35.3 | 179.3 / 185.4 | 117.7 / 130.0 | +146 |
| fixture: buttons.html | scroll | 50 | 32.7 / 34.7 | 167.9 / 184.1 | 117.3 / 124.5 | +135 |
| fixture: buttons.html | navigate | 50 | 5.3 / 5.6 | 176.2 / 183.9 | 121.9 / 127.2 | +171 |
| fixture: form.html | type | 50 | 0.9 / 1.4 | 167.0 / 173.6 | 116.3 / 118.4 | +166 |
| fixture: form.html | click | 50 | 33.4 / 34.4 | 176.7 / 190.0 | 118.3 / 132.7 | +143 |
| fixture: long.html | scroll | 50 | 32.5 / 34.0 | 203.5 / 215.1 | 148.4 / 154.9 | +171 |
| real: wikipedia (4.2k el) | click | 50 | 33.5 / 34.6 | 283.1 / 289.1 | 233.1 / 236.9 | +250 |
| real: wikipedia (4.2k el) | type | 50 | 2.1 / 3.3 | 290.3 / 300.1 | 232.2 / 237.9 | +288 |
| real: wikipedia (4.2k el) | scroll | 50 | 32.8 / 34.2 | 333.1 / 343.1 | 250.2 / 259.8 | +300 |
| real: wikipedia (4.2k el) | navigate | 50 | 43.1 / 47.7 | 317.4 / 334.2 | 265.9 / 278.7 | +274 |
| real: github (2.9k el) | click | 50 | 33.6 / 34.9 | 278.3 / 290.8 | 216.9 / 224.3 | +245 |
| real: github (2.9k el) | type | 50 | 1.6 / 3.5 | 282.4 / 287.2 | 219.7 / 225.6 | +281 |
| real: github (2.9k el) | scroll | 50 | 32.8 / 34.1 | 317.3 / 334.2 | 235.5 / 246.3 | +284 |
| real: github (2.9k el) | navigate | 50 | 64.7 / 74.5 | 338.9 / 359.1 | 276.4 / 300.3 | +274 |
| real: mdn (1.5k el) | click | 50 | 33.4 / 34.2 | 218.6 / 234.0 | 152.5 / 168.5 | +185 |
| real: mdn (1.5k el) | scroll | 50 | 32.9 / 33.8 | 247.0 / 255.0 | 183.5 / 186.9 | +214 |
| real: mdn (1.5k el) | navigate | 50 | 25.9 / 28.4 | 233.5 / 247.6 | 166.6 / 171.8 | +208 |

## Stage breakdown (receipt arms, p50 ms, from `Receipt.timing`)

| page | action | arm | capture_before | dispatch | settle | capture_after | diff+verdict | total | verdicts |
|---|---|---|---|---|---|---|---|---|---|
| fixture: buttons.html | click | receipt | 32.0 | 14.9 | 90.4 | 27.8 | 1.9 | 179.3 | no_op:50 |
| fixture: buttons.html | click | receipt_noshot | 2.6 | 15.7 | 92.0 | 4.6 | 0.9 | 117.7 | no_op:50 |
| fixture: buttons.html | scroll | receipt | 33.1 | 12.5 | 92.9 | 25.5 | 1.8 | 167.9 | no_op:50 |
| fixture: buttons.html | scroll | receipt_noshot | 1.2 | 7.4 | 107.2 | 1.7 | 1.3 | 117.3 | no_op:50 |
| fixture: buttons.html | navigate | receipt | 30.9 | 6.5 | 111.4 | 25.3 | 1.9 | 176.2 | navigated:50 |
| fixture: buttons.html | navigate | receipt_noshot | 1.2 | 6.3 | 111.0 | 2.0 | 1.2 | 121.9 | navigated:50 |
| fixture: form.html | type | receipt | 30.5 | 2.0 | 107.5 | 24.7 | 2.0 | 167.0 | changed:50 |
| fixture: form.html | type | receipt_noshot | 2.5 | 1.4 | 107.1 | 4.0 | 0.8 | 116.3 | changed:50 |
| fixture: form.html | click | receipt | 32.3 | 15.0 | 89.4 | 26.9 | 2.0 | 176.7 | no_op:50 |
| fixture: form.html | click | receipt_noshot | 2.4 | 15.3 | 92.8 | 4.3 | 0.8 | 118.3 | no_op:50 |
| fixture: long.html | scroll | receipt | 29.0 | 13.9 | 134.2 | 23.0 | 1.8 | 203.5 | changed:50 |
| fixture: long.html | scroll | receipt_noshot | 1.0 | 11.4 | 133.3 | 1.5 | 1.2 | 148.4 | changed:50 |
| real: wikipedia (4.2k el) | click | receipt | 83.9 | 23.2 | 89.2 | 84.8 | 1.9 | 283.1 | no_op:50 |
| real: wikipedia (4.2k el) | click | receipt_noshot | 57.9 | 10.4 | 106.6 | 57.9 | 0.9 | 233.1 | no_op:50 |
| real: wikipedia (4.2k el) | type | receipt | 87.1 | 4.5 | 107.8 | 87.2 | 3.0 | 290.3 | changed:50 |
| real: wikipedia (4.2k el) | type | receipt_noshot | 58.1 | 2.7 | 109.2 | 59.2 | 1.8 | 232.2 | changed:50 |
| real: wikipedia (4.2k el) | scroll | receipt | 82.2 | 24.4 | 133.6 | 90.0 | 2.0 | 333.1 | changed:50 |
| real: wikipedia (4.2k el) | scroll | receipt_noshot | 53.1 | 6.7 | 133.5 | 53.3 | 2.2 | 250.2 | changed:50 |
| real: wikipedia (4.2k el) | navigate | receipt | 84.8 | 9.4 | 127.5 | 96.2 | 2.3 | 317.4 | navigated:50 |
| real: wikipedia (4.2k el) | navigate | receipt_noshot | 59.2 | 15.6 | 127.3 | 61.2 | 2.8 | 265.9 | navigated:50 |
| real: github (2.9k el) | click | receipt | 82.4 | 16.5 | 91.1 | 83.9 | 1.9 | 278.3 | no_op:50 |
| real: github (2.9k el) | click | receipt_noshot | 52.1 | 12.7 | 105.7 | 52.0 | 0.9 | 216.9 | no_op:50 |
| real: github (2.9k el) | type | receipt | 81.4 | 5.0 | 110.1 | 82.0 | 2.6 | 282.4 | changed:50 |
| real: github (2.9k el) | type | receipt_noshot | 51.9 | 2.4 | 110.6 | 52.5 | 1.7 | 219.7 | changed:50 |
| real: github (2.9k el) | scroll | receipt | 81.6 | 21.9 | 131.8 | 88.9 | 2.1 | 317.3 | changed:50 |
| real: github (2.9k el) | scroll | receipt_noshot | 47.6 | 5.8 | 130.6 | 47.9 | 2.2 | 235.5 | changed:50 |
| real: github (2.9k el) | navigate | receipt | 80.3 | 14.1 | 154.5 | 84.6 | 4.1 | 338.9 | navigated:50 |
| real: github (2.9k el) | navigate | receipt_noshot | 50.3 | 14.2 | 153.5 | 51.7 | 5.5 | 276.4 | navigated:50 |
| real: mdn (1.5k el) | click | receipt | 51.3 | 16.5 | 97.3 | 51.6 | 2.2 | 218.6 | no_op:50 |
| real: mdn (1.5k el) | click | receipt_noshot | 20.7 | 14.8 | 89.2 | 22.0 | 0.9 | 152.5 | no_op:50 |
| real: mdn (1.5k el) | scroll | receipt | 59.8 | 6.3 | 131.8 | 47.8 | 2.1 | 247.0 | changed:50 |
| real: mdn (1.5k el) | scroll | receipt_noshot | 17.5 | 11.0 | 133.7 | 19.3 | 1.4 | 183.5 | changed:50 |
| real: mdn (1.5k el) | navigate | receipt | 48.1 | 11.1 | 112.6 | 58.9 | 2.5 | 233.5 | navigated:50 |
| real: mdn (1.5k el) | navigate | receipt_noshot | 20.3 | 9.5 | 112.0 | 22.8 | 2.4 | 166.6 | navigated:50 |

`diff+verdict` = total − (capture_before + dispatch + settle + capture_after). `settle` includes the mandatory 100 ms quiet window.

## Environment

| env | value |
|---|---|
| date_utc | 2026-09-02T20:59:19+00:00 |
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
| loadavg | 1.10 1.46 1.98 3/2629 1250019 |
