# Real-site smoke (one pass, live)

Run at 2026-09-02T20:15:42+00:00. Pages and actions are listed in `bench/realsite.py`.

| page | action | verdict | evidence | settled_by / settle ms | receipt total ms |
|---|---|---|---|---|---|
| Wikipedia article (4186 el) | scroll {'direction': 'down', 'pages': 1.0} | changed | window_scrolled: dx=0 dy=800; dom_changed: 2 nodes (+0 -0 ~2 listed); screenshot_changed: 0.316 of pixels, dhash distance 22 | dom 133 ms | 451 (capture 132) |
| Wikipedia article (4186 el) | type {'selector': '#searchInput, input[type=search]', 'text': 'Firefox'} | changed | window_scrolled: dx=0 dy=-800; value_changed: '' -> 'Firefox'; focus_changed: None -> form#searchform>div:nth-of-type(1)>div:nth-of-type(1)>div:nth-of-type(1)>i | dom 638 ms | 916 (capture 113) |
| MDN reference page (1535 el) | scroll {'direction': 'down', 'pages': 1.0} | changed | window_scrolled: dx=0 dy=800; dom_changed: 30 nodes (+12 -0 ~1 listed); screenshot_changed: 0.273 of pixels, dhash distance 13 | scroll 131 ms | 935 (capture 391) |
| MDN reference page (1535 el) | click {'selector': "a[href='/en-US/docs/Web/API']"} | blocked | target_not_visible: div#uid_dojvkwxu816>p:nth-of-type(1)>a:nth-of-type(1); dispatch_error: TimeoutError: Locator.click: Timeout 300ms exceeded. (element is not  | quiet_window 1 ms | 1118 (capture 390) |
| Hacker News front page (810 el) | scroll {'direction': 'down', 'pages': 1.0} | changed | window_scrolled: dx=0 dy=379; screenshot_changed: 0.103 of pixels, dhash distance 4 | scroll 121 ms | 269 (capture 64) |
| Hacker News front page (810 el) | click {'selector': 'a.morelink'} | navigated | url_changed: https://news.ycombinator.com/ -> https://news.ycombinator.com/?p=2; navigation_events: 1 (cross-document: 1); window_scrolled: dx=0 dy=-379; dom_ch | dom 117 ms | 402 (capture 84) |
| GitHub repo page (2909 el) | scroll {'direction': 'down', 'pages': 1.0} | changed | never_settled: still busy ['animations'] after 10001ms (timeout 10000ms); window_scrolled: dx=0 dy=800; screenshot_changed: 0.293 of pixels, dhash distance 18 | animations 10001 ms TIMEOUT busy=['animations'] | 10297 (capture 144) |
| GitHub repo page (2909 el) | click {'selector': "a#issues-tab, a[href='/microsoft/playwright/issues']"} | navigated | url_changed: https://github.com/microsoft/playwright -> https://github.com/microsoft/playwright/issues; navigation_events: 4 (cross-document: 0); title_changed; | network 2221 ms | 2664 (capture 177) |
| httpbin HTML form (46 el) | type {'selector': 'input[name=custname]', 'text': 'Ada'} | changed | value_changed: '' -> 'Ada'; focus_changed: None -> html>body:nth-of-type(1)>form:nth-of-type(1)>p:nth-of-type(1)>label:nth-of-type(1)>input:nth-of-type(1); dom_ | quiet_window 105 ms | 190 (capture 32) |
| httpbin HTML form (46 el) | click {'selector': 'input[value=medium]'} | changed | focus_changed: html>body:nth-of-type(1)>form:nth-of-type(1)>p:nth-of-type(1)>label:nth-of-type(1)>input:nth-of-type(1) -> html>body:nth-of-type(1)>form:nth-of-t | quiet_window 107 ms | 184 (capture 32) |

## Environment

| env | value |
|---|---|
| date_utc | 2026-09-02T20:15:42+00:00 |
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
| lib_hash | 4de1b059b50c |
| loadavg | 2.67 3.68 3.94 3/2424 1108977 |
