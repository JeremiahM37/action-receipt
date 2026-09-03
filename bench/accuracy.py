"""Bench 3 - verdict accuracy on a labeled corpus.

    python -m bench.accuracy [--repeats 3]

Every case is (page, optional setup JS, action, args) with a ground-truth verdict labelled from
what the page *does*, not from what the library returns. Hard negatives are tagged. Each case
runs `repeats` times on a fresh page; the confusion matrix counts every trial, and every miss
is listed with the receipt's evidence and hint.

Session config: quiet_ms=100 (default), settle timeout 3000 ms (default is 10000 - lowered
only so that the never-settling pages do not cost 10 s per trial; it changes nothing for pages
that settle).
"""

from __future__ import annotations

import argparse
import asyncio

from ._common import FixtureServer, browser_session, env_info, env_md, log, md_table, write_results

VERDICTS = ["changed", "no_op", "navigated", "blocked", "unknown"]

C = "acc/controls.html"
K = "acc/clock.html"
S = "acc/spa.html"
R = "acc/scroll.html"


def case(id, page, action, expect, tag="", setup=None, note="", **args):
    return {
        "id": id,
        "page": page,
        "action": action,
        "args": args,
        "expect": expect,
        "tag": tag,
        "setup": setup,
        "note": note,
    }


CASES = [
    # --- controls: positives
    case("c01", C, "click", "changed", selector="#counter"),
    case("c09", C, "click", "changed", selector="#checkbox"),
    case("c11", C, "click", "changed", selector="#radio-b"),
    case("c12", C, "press", "changed", key="ArrowDown", selector="#select"),
    case("c13", C, "type", "changed", selector="#text", text="hello"),
    case("c16", C, "type", "changed", selector="#textarea", text="hello"),
    case("c17", C, "type", "changed", selector="#ce", text="hello", note="contenteditable"),
    case("c21", C, "click", "changed", selector="#summary", note="<details> toggle"),
    case("c22", C, "click", "changed", selector="#tab2", note="aria-selected flips"),
    case("c24", C, "click", "changed", selector="#dropdown", note="class toggle shows a menu"),
    case("c25", C, "click", "changed", selector="#alert", note="JS alert"),
    case("c26", C, "click", "changed", selector="#confirm", note="JS confirm, auto-accepted"),
    case("c27", C, "click", "changed", selector="#modal"),
    case(
        "c28",
        C,
        "press",
        "changed",
        key="Escape",
        setup="document.getElementById('overlay').classList.add('on')",
        note="Escape closes the modal",
    ),
    case(
        "c29",
        C,
        "click",
        "changed",
        selector="#close-modal",
        setup="document.getElementById('overlay').classList.add('on')",
    ),
    case("c30", C, "press", "changed", key="Tab", note="focus moves; counts for press"),
    case("c36", C, "click", "changed", selector="#hash-plain", note="href='#': URL fragment changes"),
    case("c37", C, "click", "changed", selector="#slow", note="800 ms fetch then DOM write"),
    case("c38", C, "click", "changed", selector="#delayed-50", note="setTimeout 50 ms"),
    case(
        "c39",
        C,
        "click",
        "changed",
        selector="#delayed-300",
        tag="hard-positive",
        note="setTimeout 300 ms, nothing keeps the page busy - documented limitation",
    ),
    case("c40", C, "click", "changed", selector="#canvas", note="canvas paint: screenshot only"),
    case("c44", C, "click", "changed", selector="#submit-mark", note="JS validation adds a class"),
    case("c45", C, "scroll", "changed", direction="down", note="controls page is taller than the viewport"),
    # --- controls: no_op
    case("c02", C, "click", "no_op", selector="#noop"),
    case("c10", C, "click", "no_op", selector="#radio-a", tag="hard-negative", note="radio already checked"),
    case(
        "c15",
        C,
        "type",
        "no_op",
        selector="#full",
        text="x",
        tag="hard-negative",
        note="maxlength reached: keystrokes rejected",
    ),
    case(
        "c19",
        C,
        "click",
        "no_op",
        selector="#hover",
        tag="hard-negative",
        note="cosmetic: :hover colour only, no handler",
    ),
    case(
        "c20",
        C,
        "click",
        "no_op",
        selector="#hover-huge",
        tag="hard-negative",
        note="cosmetic: :hover colour on a 320px block (>2% of pixels)",
    ),
    case("c23", C, "click", "no_op", selector="#tab1", tag="hard-negative", note="tab already selected"),
    case("c31", C, "press", "no_op", key="Enter", note="nothing focused"),
    case("c32", C, "click", "no_op", selector="#para"),
    case("c33", C, "click", "no_op", selector="#h1"),
    case("c34", C, "click", "no_op", selector="#js-void", note="javascript:void(0) link"),
    case("c35", C, "click", "no_op", selector="#hash-pd", note="href='#' with preventDefault"),
    case(
        "c41",
        C,
        "click",
        "blocked",
        selector="#submit-required",
        tag="hard-negative",
        note="HTML5 required-field validation blocks submit; no DOM change (F10: the browser's refusal is `blocked` with the field named, was labelled no_op)",
    ),
    case(
        "c43",
        C,
        "click",
        "no_op",
        selector="#submit-silent",
        tag="hard-negative",
        note="onsubmit returns false silently",
    ),
    # --- controls: blocked
    case("c03", C, "click", "blocked", selector="#disabled"),
    case("c04", C, "click", "blocked", selector="#aria-disabled", note="aria-disabled=true"),
    case("c05", C, "click", "blocked", selector="#covered", note="covered by an overlay div"),
    case("c06", C, "click", "blocked", selector="#hidden", note="display:none"),
    case("c07", C, "click", "blocked", selector="#offscreen", note="positioned at left:-9999px"),
    case("c08", C, "click", "blocked", selector="#pe-none", note="pointer-events:none"),
    case("c14", C, "type", "blocked", selector="#readonly", text="nope"),
    case("c18", C, "type", "blocked", selector="#fake", text="hello", note="a div styled as an input"),
    # --- controls: navigated
    case("c42", C, "click", "navigated", selector="#submit-ok", note="valid GET form submit"),
    # --- background activity (hard negatives: unrelated click on a page that is never still)
    case(
        "k01",
        K + "?bg=clock",
        "click",
        "no_op",
        selector="#noop",
        tag="hard-negative",
        note="clock ticks every 1 s",
    ),
    case(
        "k02",
        K + "?bg=clock",
        "click",
        "changed",
        selector="#counter",
        note="counter on the ticking-clock page",
    ),
    case(
        "k03",
        K + "?bg=fast",
        "click",
        "no_op",
        selector="#noop",
        tag="hard-negative",
        note="ticker every 50 ms: never quiet; library documents unknown",
    ),
    case(
        "k04",
        K + "?bg=fast",
        "click",
        "changed",
        selector="#counter",
        note="counter on the 50 ms ticker page",
    ),
    case(
        "k05",
        K + "?bg=spinner",
        "click",
        "no_op",
        selector="#noop",
        tag="hard-negative",
        note="perpetual CSS spinner (animations never quiet)",
    ),
    case(
        "k06", K + "?bg=spinner", "click", "changed", selector="#counter", note="counter on the spinner page"
    ),
    case(
        "k07",
        K + "?bg=raf",
        "click",
        "no_op",
        selector="#noop",
        tag="hard-negative",
        note="rAF loop writing inline style",
    ),
    case(
        "k08",
        K + "?bg=none",
        "click",
        "no_op",
        selector="#noop",
        note="control: same page, no background activity",
    ),
    # --- SPA transitions
    case("s01", S, "click", "navigated", selector="#push-about", note="pushState to a new path"),
    case(
        "s02",
        S,
        "click",
        "no_op",
        selector="#replace-same-identical",
        tag="hard-negative",
        note="identical-URL replaceState + identical re-render",
    ),
    case(
        "s03",
        S,
        "click",
        "changed",
        selector="#replace-same-swap",
        tag="hard-positive",
        note="identical URL, content swapped",
    ),
    case("s04", S, "click", "changed", selector="#hash-about", note="hash route + content swap"),
    case("s05", S, "click", "navigated", selector="#reload", note="location.reload()"),
    case("s06", S, "click", "navigated", selector="#self-link", note="link to the same URL"),
    case(
        "s07",
        S,
        "click",
        "navigated",
        selector="#assign-late",
        tag="hard-positive",
        note="location.assign after a 250 ms timer",
    ),
    case("s08", S, "click", "navigated", selector="#assign-now"),
    case("s09", S, "click", "navigated", selector="#winopen", note="window.open new tab"),
    # --- scroll
    case("r01", "lib/short.html", "scroll", "no_op", direction="down", note="page not scrollable"),
    case("r02", "lib/long.html", "scroll", "changed", direction="down"),
    case(
        "r03",
        "lib/long.html",
        "scroll",
        "no_op",
        direction="down",
        tag="hard-negative",
        setup="window.scrollTo(0, document.scrollingElement.scrollHeight)",
        note="already at the bottom",
    ),
    case(
        "r04",
        "lib/long.html",
        "scroll",
        "no_op",
        direction="up",
        tag="hard-negative",
        note="already at the top",
    ),
    case(
        "r05",
        "lib/inner-scroll.html",
        "scroll",
        "no_op",
        direction="down",
        tag="hard-negative",
        note="window scroll on overflow:hidden page; only #panel scrolls",
    ),
    case(
        "r06", "lib/inner-scroll.html", "scroll", "changed", direction="down", pages=0.25, selector="#panel"
    ),
    case(
        "r07",
        "lib/inner-scroll.html",
        "scroll",
        "no_op",
        direction="down",
        selector="#panel",
        tag="hard-negative",
        setup="document.getElementById('panel').scrollTop = 1e6",
        note="container already at its bottom",
    ),
    case("r08", R, "scroll", "changed", direction="down", selector="#bigbox"),
    case(
        "r09",
        R,
        "scroll",
        "no_op",
        direction="down",
        selector="#smallbox",
        tag="hard-negative",
        note="overflow:auto box whose content fits",
    ),
    case("r10", R + "?v=wide", "scroll", "changed", direction="right", note="horizontal window scroll"),
    case("r11", R + "?v=wide", "scroll", "no_op", direction="down", note="only horizontally scrollable"),
    case(
        "r12", R + "?v=exact", "scroll", "no_op", direction="down", note="content exactly one viewport tall"
    ),
    # --- navigation
    case("n01", "lib/nav.html", "click", "navigated", selector="#go"),
    case("n02", "lib/nav.html", "click", "changed", selector="#frag", note="fragment link that scrolls"),
    case("n03", "lib/nav.html", "click", "navigated", selector="#newtab"),
    case("n04", "lib/nav.html", "navigate", "navigated", url="lib/short.html"),
    case(
        "n05",
        "lib/nav.html",
        "navigate",
        "navigated",
        url="lib/nav.html",
        note="navigate to the same URL (reload)",
    ),
]


async def run_case(sess, base, c, prescroll=False):
    url = f"{base}/{c['page']}"
    page = await sess.new_page(url)
    try:
        if c["setup"]:
            await page.evaluate(c["setup"])
            await asyncio.sleep(0.15)
        a, args = c["action"], c["args"]
        if prescroll and a in ("click", "type", "press") and args.get("selector"):
            # bring the target into view BEFORE the action so Playwright's own scroll-into-view
            # cannot be mistaken for an effect of the action
            try:
                await page.locator(args["selector"]).first.evaluate(
                    "el => el.scrollIntoView({block: 'center'})", timeout=1000
                )
                await asyncio.sleep(0.15)
            except Exception:
                pass
        if a == "click":
            _, r = await sess.click(args["selector"], page=page)
        elif a == "type":
            _, r = await sess.type(args["selector"], args["text"], page=page, clear=args.get("clear", False))
        elif a == "press":
            _, r = await sess.press(args["key"], selector=args.get("selector"), page=page)
        elif a == "scroll":
            _, r = await sess.scroll(
                direction=args.get("direction", "down"),
                pages=args.get("pages", 1.0),
                selector=args.get("selector"),
                page=page,
            )
        elif a == "navigate":
            _, r = await sess.navigate(f"{base}/{args['url']}", page=page)
        else:
            raise ValueError(a)
        return {
            "verdict": r.verdict.verdict,
            "evidence": r.verdict.evidence[:6],
            "hint": r.verdict.hint,
            "settled_by": r.settlement.settled_by,
            "settle_ms": round(r.settlement.elapsed_ms),
            "timed_out": r.settlement.timed_out,
            "dispatch_error": r.dispatch.get("error"),
            "screenshot_fraction": r.delta.screenshot_changed_fraction,
        }
    finally:
        for p in list(sess.pages()):
            try:
                await p.close()
            except Exception:
                pass


async def run(repeats: int, prescroll: bool = False, only: set[str] | None = None):
    out = []
    async with (
        FixtureServer() as srv,
        browser_session(viewport=(1000, 700), quiet_ms=100, timeout_ms=3000) as (_pw, browser, _ctx, sess),
    ):
        for c in CASES:
            if only and c["id"] not in only:
                continue
            trials = []
            for i in range(repeats):
                try:
                    trials.append(await run_case(sess, srv.base_url, c, prescroll=prescroll))
                except Exception as e:
                    trials.append(
                        {
                            "verdict": "ERROR",
                            "evidence": [f"{type(e).__name__}: {str(e)[:160]}"],
                            "hint": None,
                        }
                    )
            got = [t["verdict"] for t in trials]
            log(f"{c['id']:4s} expect={c['expect']:9s} got={got}")
            out.append({**c, "trials": trials})
        chromium = browser.version
    return out, chromium


def summarize(cases, repeats):
    labels = VERDICTS + ["ERROR"]
    matrix = {e: dict.fromkeys(labels, 0) for e in VERDICTS}
    rows = []
    misses = []
    flaky = 0
    for c in cases:
        got = [t["verdict"] for t in c["trials"]]
        for g in got:
            matrix[c["expect"]][g if g in labels else "ERROR"] += 1
        consistent = len(set(got)) == 1
        flaky += 0 if consistent else 1
        ok = sum(1 for g in got if g == c["expect"])
        rows.append(
            [
                c["id"],
                c["tag"] or "",
                c["page"].split("?")[0].split("/")[-1]
                + ("?" + c["page"].split("?")[1] if "?" in c["page"] else ""),
                c["action"] + " " + ", ".join(f"{k}={v}" for k, v in c["args"].items()),
                c["expect"],
                "/".join(got),
                f"{ok}/{len(got)}",
                "" if consistent else "flaky",
                c["note"],
            ]
        )
        if ok < len(got):
            for t in c["trials"]:
                if t["verdict"] != c["expect"]:
                    misses.append(
                        [
                            c["id"],
                            c["tag"] or "",
                            c["expect"],
                            t["verdict"],
                            "; ".join(t.get("evidence", []))[:220],
                            (t.get("hint") or "")[:160],
                            f"{t.get('settled_by')}/{t.get('settle_ms')}ms"
                            + (" TIMEOUT" if t.get("timed_out") else ""),
                        ]
                    )
                    break  # one representative miss per case; all trials are in the JSON
    total = sum(sum(r.values()) for r in matrix.values())
    correct = sum(matrix[v][v] for v in VERDICTS)
    prf = []
    for v in VERDICTS:
        tp = matrix[v][v]
        pred = sum(matrix[e][v] for e in VERDICTS)
        act = sum(matrix[v].values())
        prf.append([v, act, pred, f"{tp / pred:.2f}" if pred else "-", f"{tp / act:.2f}" if act else "-"])
    hard = [c for c in cases if c["tag"]]
    hard_ok = sum(1 for c in hard for t in c["trials"] if t["verdict"] == c["expect"])
    hard_n = sum(len(c["trials"]) for c in hard)
    md = [
        "# Verdict accuracy",
        "",
        f"{len(cases)} labelled cases × {repeats} trials = {total} verdicts. Accuracy **{correct}/{total} = {100 * correct / total:.1f}%**; "
        f"on the {len(hard)} hard cases (tagged) {hard_ok}/{hard_n} = {100 * hard_ok / hard_n:.1f}%. {flaky} cases gave different verdicts across trials.",
        "",
        "## Confusion matrix (rows = ground truth, columns = receipt verdict)",
        "",
        md_table(["truth \\ verdict"] + labels, [[e] + [matrix[e][g] for g in labels] for e in VERDICTS]),
        "",
        "## Precision / recall per verdict",
        "",
        md_table(["verdict", "actual", "predicted", "precision", "recall"], prf),
        "",
        "## Misses (one representative trial per case; every trial is in accuracy.json)",
        "",
        md_table(["case", "tag", "expected", "got", "evidence", "hint", "settled_by/ms"], misses)
        if misses
        else "none",
        "",
        "## All cases",
        "",
        md_table(["case", "tag", "page", "action", "expected", "got (per trial)", "ok", "", "note"], rows),
        "",
    ]
    return {
        "matrix": matrix,
        "accuracy": correct / total,
        "hard_accuracy": hard_ok / hard_n if hard_n else None,
        "flaky_cases": flaky,
        "misses": misses,
    }, "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument(
        "--prescroll",
        action="store_true",
        help="scroll each target into view before acting (isolates Playwright's scroll-into-view from the verdict); writes accuracy_prescroll.*",
    )
    ap.add_argument(
        "--only",
        default="",
        help="comma-separated case ids to run (default: every case); used by the smoke subset",
    )
    args = ap.parse_args(argv)
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    if only:
        unknown = only - {c["id"] for c in CASES}
        if unknown:
            ap.error(f"unknown case id(s): {sorted(unknown)}")
    cases, chromium = asyncio.run(run(args.repeats, prescroll=args.prescroll, only=only or None))
    summary, md = summarize(cases, args.repeats)
    if args.prescroll:
        md = md.replace("# Verdict accuracy", "# Verdict accuracy (targets pre-scrolled into view)", 1)
    info = env_info(
        {
            "chromium": chromium,
            "repeats": args.repeats,
            "quiet_ms": 100,
            "settle_timeout_ms": 3000,
            "prescroll": args.prescroll,
        }
    )
    md += "\n## Environment\n\n" + env_md(info) + "\n"
    jp, mp = write_results(
        "accuracy_prescroll" if args.prescroll else "accuracy",
        {"env": info, "summary": summary, "cases": cases},
        md,
    )
    log(f"wrote {jp} and {mp}")
    print(md)


if __name__ == "__main__":
    main()
