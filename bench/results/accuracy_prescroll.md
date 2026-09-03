# Verdict accuracy (targets pre-scrolled into view)

79 labelled cases × 3 trials = 237 verdicts. Accuracy **237/237 = 100.0%**; on the 20 hard cases (tagged) 60/60 = 100.0%. 0 cases gave different verdicts across trials.

## Confusion matrix (rows = ground truth, columns = receipt verdict)

| truth \ verdict | changed | no_op | navigated | blocked | unknown | ERROR |
|---|---|---|---|---|---|---|
| changed | 99 | 0 | 0 | 0 | 0 | 0 |
| no_op | 0 | 78 | 0 | 0 | 0 | 0 |
| navigated | 0 | 0 | 33 | 0 | 0 | 0 |
| blocked | 0 | 0 | 0 | 27 | 0 | 0 |
| unknown | 0 | 0 | 0 | 0 | 0 | 0 |

## Precision / recall per verdict

| verdict | actual | predicted | precision | recall |
|---|---|---|---|---|
| changed | 99 | 99 | 1.00 | 1.00 |
| no_op | 78 | 78 | 1.00 | 1.00 |
| navigated | 33 | 33 | 1.00 | 1.00 |
| blocked | 27 | 27 | 1.00 | 1.00 |
| unknown | 0 | 0 | - | - |

## Misses (one representative trial per case; every trial is in accuracy.json)

none

## All cases

| case | tag | page | action | expected | got (per trial) | ok |  | note |
|---|---|---|---|---|---|---|---|---|
| c01 |  | controls.html | click selector=#counter | changed | changed/changed/changed | 3/3 |  |  |
| c09 |  | controls.html | click selector=#checkbox | changed | changed/changed/changed | 3/3 |  |  |
| c11 |  | controls.html | click selector=#radio-b | changed | changed/changed/changed | 3/3 |  |  |
| c12 |  | controls.html | press key=ArrowDown, selector=#select | changed | changed/changed/changed | 3/3 |  |  |
| c13 |  | controls.html | type selector=#text, text=hello | changed | changed/changed/changed | 3/3 |  |  |
| c16 |  | controls.html | type selector=#textarea, text=hello | changed | changed/changed/changed | 3/3 |  |  |
| c17 |  | controls.html | type selector=#ce, text=hello | changed | changed/changed/changed | 3/3 |  | contenteditable |
| c21 |  | controls.html | click selector=#summary | changed | changed/changed/changed | 3/3 |  | <details> toggle |
| c22 |  | controls.html | click selector=#tab2 | changed | changed/changed/changed | 3/3 |  | aria-selected flips |
| c24 |  | controls.html | click selector=#dropdown | changed | changed/changed/changed | 3/3 |  | class toggle shows a menu |
| c25 |  | controls.html | click selector=#alert | changed | changed/changed/changed | 3/3 |  | JS alert |
| c26 |  | controls.html | click selector=#confirm | changed | changed/changed/changed | 3/3 |  | JS confirm, auto-accepted |
| c27 |  | controls.html | click selector=#modal | changed | changed/changed/changed | 3/3 |  |  |
| c28 |  | controls.html | press key=Escape | changed | changed/changed/changed | 3/3 |  | Escape closes the modal |
| c29 |  | controls.html | click selector=#close-modal | changed | changed/changed/changed | 3/3 |  |  |
| c30 |  | controls.html | press key=Tab | changed | changed/changed/changed | 3/3 |  | focus moves; counts for press |
| c36 |  | controls.html | click selector=#hash-plain | changed | changed/changed/changed | 3/3 |  | href='#': URL fragment changes |
| c37 |  | controls.html | click selector=#slow | changed | changed/changed/changed | 3/3 |  | 800 ms fetch then DOM write |
| c38 |  | controls.html | click selector=#delayed-50 | changed | changed/changed/changed | 3/3 |  | setTimeout 50 ms |
| c39 | hard-positive | controls.html | click selector=#delayed-300 | changed | changed/changed/changed | 3/3 |  | setTimeout 300 ms, nothing keeps the page busy - documented limitation |
| c40 |  | controls.html | click selector=#canvas | changed | changed/changed/changed | 3/3 |  | canvas paint: screenshot only |
| c44 |  | controls.html | click selector=#submit-mark | changed | changed/changed/changed | 3/3 |  | JS validation adds a class |
| c45 |  | controls.html | scroll direction=down | changed | changed/changed/changed | 3/3 |  | controls page is taller than the viewport |
| c02 |  | controls.html | click selector=#noop | no_op | no_op/no_op/no_op | 3/3 |  |  |
| c10 | hard-negative | controls.html | click selector=#radio-a | no_op | no_op/no_op/no_op | 3/3 |  | radio already checked |
| c15 | hard-negative | controls.html | type selector=#full, text=x | no_op | no_op/no_op/no_op | 3/3 |  | maxlength reached: keystrokes rejected |
| c19 | hard-negative | controls.html | click selector=#hover | no_op | no_op/no_op/no_op | 3/3 |  | cosmetic: :hover colour only, no handler |
| c20 | hard-negative | controls.html | click selector=#hover-huge | no_op | no_op/no_op/no_op | 3/3 |  | cosmetic: :hover colour on a 320px block (>2% of pixels) |
| c23 | hard-negative | controls.html | click selector=#tab1 | no_op | no_op/no_op/no_op | 3/3 |  | tab already selected |
| c31 |  | controls.html | press key=Enter | no_op | no_op/no_op/no_op | 3/3 |  | nothing focused |
| c32 |  | controls.html | click selector=#para | no_op | no_op/no_op/no_op | 3/3 |  |  |
| c33 |  | controls.html | click selector=#h1 | no_op | no_op/no_op/no_op | 3/3 |  |  |
| c34 |  | controls.html | click selector=#js-void | no_op | no_op/no_op/no_op | 3/3 |  | javascript:void(0) link |
| c35 |  | controls.html | click selector=#hash-pd | no_op | no_op/no_op/no_op | 3/3 |  | href='#' with preventDefault |
| c41 | hard-negative | controls.html | click selector=#submit-required | blocked | blocked/blocked/blocked | 3/3 |  | HTML5 required-field validation blocks submit; no DOM change (F10: the browser's refusal is `blocked` with the field named, was labelled no_op) |
| c43 | hard-negative | controls.html | click selector=#submit-silent | no_op | no_op/no_op/no_op | 3/3 |  | onsubmit returns false silently |
| c03 |  | controls.html | click selector=#disabled | blocked | blocked/blocked/blocked | 3/3 |  |  |
| c04 |  | controls.html | click selector=#aria-disabled | blocked | blocked/blocked/blocked | 3/3 |  | aria-disabled=true |
| c05 |  | controls.html | click selector=#covered | blocked | blocked/blocked/blocked | 3/3 |  | covered by an overlay div |
| c06 |  | controls.html | click selector=#hidden | blocked | blocked/blocked/blocked | 3/3 |  | display:none |
| c07 |  | controls.html | click selector=#offscreen | blocked | blocked/blocked/blocked | 3/3 |  | positioned at left:-9999px |
| c08 |  | controls.html | click selector=#pe-none | blocked | blocked/blocked/blocked | 3/3 |  | pointer-events:none |
| c14 |  | controls.html | type selector=#readonly, text=nope | blocked | blocked/blocked/blocked | 3/3 |  |  |
| c18 |  | controls.html | type selector=#fake, text=hello | blocked | blocked/blocked/blocked | 3/3 |  | a div styled as an input |
| c42 |  | controls.html | click selector=#submit-ok | navigated | navigated/navigated/navigated | 3/3 |  | valid GET form submit |
| k01 | hard-negative | clock.html?bg=clock | click selector=#noop | no_op | no_op/no_op/no_op | 3/3 |  | clock ticks every 1 s |
| k02 |  | clock.html?bg=clock | click selector=#counter | changed | changed/changed/changed | 3/3 |  | counter on the ticking-clock page |
| k03 | hard-negative | clock.html?bg=fast | click selector=#noop | no_op | no_op/no_op/no_op | 3/3 |  | ticker every 50 ms: never quiet; library documents unknown |
| k04 |  | clock.html?bg=fast | click selector=#counter | changed | changed/changed/changed | 3/3 |  | counter on the 50 ms ticker page |
| k05 | hard-negative | clock.html?bg=spinner | click selector=#noop | no_op | no_op/no_op/no_op | 3/3 |  | perpetual CSS spinner (animations never quiet) |
| k06 |  | clock.html?bg=spinner | click selector=#counter | changed | changed/changed/changed | 3/3 |  | counter on the spinner page |
| k07 | hard-negative | clock.html?bg=raf | click selector=#noop | no_op | no_op/no_op/no_op | 3/3 |  | rAF loop writing inline style |
| k08 |  | clock.html?bg=none | click selector=#noop | no_op | no_op/no_op/no_op | 3/3 |  | control: same page, no background activity |
| s01 |  | spa.html | click selector=#push-about | navigated | navigated/navigated/navigated | 3/3 |  | pushState to a new path |
| s02 | hard-negative | spa.html | click selector=#replace-same-identical | no_op | no_op/no_op/no_op | 3/3 |  | identical-URL replaceState + identical re-render |
| s03 | hard-positive | spa.html | click selector=#replace-same-swap | changed | changed/changed/changed | 3/3 |  | identical URL, content swapped |
| s04 |  | spa.html | click selector=#hash-about | changed | changed/changed/changed | 3/3 |  | hash route + content swap |
| s05 |  | spa.html | click selector=#reload | navigated | navigated/navigated/navigated | 3/3 |  | location.reload() |
| s06 |  | spa.html | click selector=#self-link | navigated | navigated/navigated/navigated | 3/3 |  | link to the same URL |
| s07 | hard-positive | spa.html | click selector=#assign-late | navigated | navigated/navigated/navigated | 3/3 |  | location.assign after a 250 ms timer |
| s08 |  | spa.html | click selector=#assign-now | navigated | navigated/navigated/navigated | 3/3 |  |  |
| s09 |  | spa.html | click selector=#winopen | navigated | navigated/navigated/navigated | 3/3 |  | window.open new tab |
| r01 |  | short.html | scroll direction=down | no_op | no_op/no_op/no_op | 3/3 |  | page not scrollable |
| r02 |  | long.html | scroll direction=down | changed | changed/changed/changed | 3/3 |  |  |
| r03 | hard-negative | long.html | scroll direction=down | no_op | no_op/no_op/no_op | 3/3 |  | already at the bottom |
| r04 | hard-negative | long.html | scroll direction=up | no_op | no_op/no_op/no_op | 3/3 |  | already at the top |
| r05 | hard-negative | inner-scroll.html | scroll direction=down | no_op | no_op/no_op/no_op | 3/3 |  | window scroll on overflow:hidden page; only #panel scrolls |
| r06 |  | inner-scroll.html | scroll direction=down, pages=0.25, selector=#panel | changed | changed/changed/changed | 3/3 |  |  |
| r07 | hard-negative | inner-scroll.html | scroll direction=down, selector=#panel | no_op | no_op/no_op/no_op | 3/3 |  | container already at its bottom |
| r08 |  | scroll.html | scroll direction=down, selector=#bigbox | changed | changed/changed/changed | 3/3 |  |  |
| r09 | hard-negative | scroll.html | scroll direction=down, selector=#smallbox | no_op | no_op/no_op/no_op | 3/3 |  | overflow:auto box whose content fits |
| r10 |  | scroll.html?v=wide | scroll direction=right | changed | changed/changed/changed | 3/3 |  | horizontal window scroll |
| r11 |  | scroll.html?v=wide | scroll direction=down | no_op | no_op/no_op/no_op | 3/3 |  | only horizontally scrollable |
| r12 |  | scroll.html?v=exact | scroll direction=down | no_op | no_op/no_op/no_op | 3/3 |  | content exactly one viewport tall |
| n01 |  | nav.html | click selector=#go | navigated | navigated/navigated/navigated | 3/3 |  |  |
| n02 |  | nav.html | click selector=#frag | changed | changed/changed/changed | 3/3 |  | fragment link that scrolls |
| n03 |  | nav.html | click selector=#newtab | navigated | navigated/navigated/navigated | 3/3 |  |  |
| n04 |  | nav.html | navigate url=lib/short.html | navigated | navigated/navigated/navigated | 3/3 |  |  |
| n05 |  | nav.html | navigate url=lib/nav.html | navigated | navigated/navigated/navigated | 3/3 |  | navigate to the same URL (reload) |

## Environment

| env | value |
|---|---|
| date_utc | 2026-09-03T10:01:43+00:00 |
| chromium | 151.0.7922.34 |
| playwright | 1.62.0 |
| mcp | 2.1.1 |
| python | 3.13.5 |
| cpu | AMD RYZEN AI MAX+ 395 w/ Radeon 8060S |
| cpu_count | 32 |
| mem_gb | 123.5 |
| kernel | 6.17.13-2-pve |
| git_head | 1b6119c |
| git_dirty | True |
| lib_hash | 6d4eae52879e |
| loadavg | 2.16 2.86 3.38 4/2366 2018044 |
