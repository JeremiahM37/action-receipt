"""Bench 1 - per-action overhead: receipt vs raw Playwright.

    python -m bench.overhead [--n 50] [--warmup 3]

For click / type / scroll / navigate on the library's fixture pages and on three heavy real
pages saved as static fixtures (bench/fixtures/real/, see SOURCES.md; every non-local request is
aborted so no live site is touched), measures per-action wall time in three arms:

* raw            - the bare Playwright call (locator.click / fill / mouse.wheel / goto load)
* receipt        - ReceiptSession.<action>() with screenshots (the MCP default)
* receipt_noshot - the same with screenshots=False

The receipt's stage breakdown comes from the library's own ``Receipt.timing``
(capture_before / dispatch / settle / capture_after); ``diff`` is total minus those four
(delta + verdict + bookkeeping). Reports median and p90 of n actions per cell.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from ._common import (
    FIXTURES,
    FixtureServer,
    browser_session,
    env_info,
    env_md,
    log,
    md_table,
    median,
    p90,
    write_results,
)

# (label, url path, {action: selector})
PAGE_SETS = [
    (
        "fixture: buttons.html",
        "lib/buttons.html",
        {"click": "#counter-btn", "scroll": None, "navigate": None},
    ),
    ("fixture: form.html", "lib/form.html", {"type": "#name", "click": "#name"}),
    ("fixture: long.html", "lib/long.html", {"scroll": None}),
    (
        "real: wikipedia (4.2k el)",
        "real/wikipedia.html",
        {"click": "h1", "type": "input[type=search], input[type=text]", "scroll": None, "navigate": None},
    ),
    (
        "real: github (2.9k el)",
        "real/github.html",
        {"click": "h1, h2", "type": "input[type=search], input[type=text]", "scroll": None, "navigate": None},
    ),
    (
        "real: mdn (1.5k el)",
        "real/mdn.html",
        {"click": "h1", "type": "input[type=search], input[type=text]", "scroll": None, "navigate": None},
    ),
]
ARMS = ["raw", "receipt", "receipt_noshot"]


async def _do_raw(page, url, action, sel, i):
    if action == "click":
        await page.locator(sel).first.click(timeout=3000)
    elif action == "type":
        await page.locator(sel).first.fill("abc" if i % 2 == 0 else "xyz", timeout=3000)
    elif action == "scroll":
        vp = page.viewport_size or {"width": 1280, "height": 800}
        await page.mouse.move(vp["width"] / 2, vp["height"] / 2)
        await page.mouse.wheel(0, (vp["height"] // 2) * (1 if i % 2 == 0 else -1))
    elif action == "navigate":
        await page.goto(url, wait_until="load")


async def _do_receipt(sess, page, url, action, sel, i):
    if action == "click":
        return await sess.click(sel, page=page)
    if action == "type":
        return await sess.type(sel, "abc" if i % 2 == 0 else "xyz", clear=True, page=page)
    if action == "scroll":
        return await sess.scroll(direction="down" if i % 2 == 0 else "up", pages=0.5, page=page)
    if action == "navigate":
        return await sess.navigate(url, page=page)
    raise ValueError(action)


async def run(n: int, warmup: int) -> dict:
    samples: dict[str, list[dict]] = {}
    cells = []
    async with FixtureServer() as srv, browser_session() as (_pw, browser, _ctx, sess):
        for label, path, actions in PAGE_SETS:
            url = srv.url(path)
            if not (FIXTURES / path).exists() and not path.startswith("lib/"):
                log(f"skip {label}: fixture missing ({path}) - run python -m bench.fetch_fixtures")
                continue
            page = await sess.new_page(url)
            elements = await page.evaluate("document.querySelectorAll('*').length")
            for action, sel in actions.items():
                if action == "type" and sel and await page.locator(sel).count() == 0:
                    log(f"skip {label} type: no text input on the page")
                    continue
                if action == "type" and sel:
                    # use the first *visible* match; skip if none is visible
                    loc = page.locator(sel).first
                    try:
                        if not await loc.is_visible():
                            log(f"skip {label} type: input not visible")
                            continue
                    except Exception:
                        continue
                for arm in ARMS:
                    key = f"{label}|{action}|{arm}"
                    samples[key] = []
                    sess.screenshots = arm != "receipt_noshot"
                    for i in range(warmup + n):
                        if action == "scroll" and i % 2 == 0 and arm == "raw":
                            await page.evaluate("window.scrollTo(0,0)")
                        t0 = time.perf_counter()
                        rec = None
                        try:
                            if arm == "raw":
                                await _do_raw(page, url, action, sel, i)
                            else:
                                _, rec = await _do_receipt(sess, page, url, action, sel, i)
                        except Exception as e:
                            if i >= warmup:
                                samples[key].append(
                                    {
                                        "wall_ms": (time.perf_counter() - t0) * 1000,
                                        "error": f"{type(e).__name__}: {str(e)[:120]}",
                                    }
                                )
                            continue
                        wall = (time.perf_counter() - t0) * 1000
                        if i < warmup:
                            continue
                        s = {"wall_ms": wall}
                        if rec is not None:
                            t = rec.timing
                            stages = {
                                k: t.get(k, 0.0)
                                for k in ("capture_before", "dispatch", "settle", "capture_after")
                            }
                            s.update(stages)
                            s["total"] = t["total"]
                            s["diff"] = t["total"] - sum(stages.values())
                            s["verdict"] = rec.verdict.verdict
                            s["settled_by"] = rec.settlement.settled_by
                        samples[key].append(s)
                    log(
                        f"{key}: n={len(samples[key])} p50={median([s['wall_ms'] for s in samples[key]]):.0f}ms"
                    )
                    cells.append(
                        {"page": label, "elements": elements, "action": action, "arm": arm, "key": key}
                    )
                sess.screenshots = True
            await page.close()
        chromium = browser.version
    return {"samples": samples, "cells": cells, "chromium": chromium}


def summarize(data: dict, n: int, warmup: int) -> tuple[dict, str]:
    rows = []
    stage_rows = []
    out_cells = []
    for c in data["cells"]:
        ss = [s for s in data["samples"][c["key"]] if "error" not in s]
        errs = len(data["samples"][c["key"]]) - len(ss)
        walls = [s["wall_ms"] for s in ss]
        cell = {
            **c,
            "n": len(ss),
            "errors": errs,
            "p50_ms": round(median(walls), 1) if walls else None,
            "p90_ms": round(p90(walls), 1) if walls else None,
        }
        if c["arm"] != "raw" and ss:
            for k in ("capture_before", "dispatch", "settle", "capture_after", "diff"):
                cell[f"{k}_p50"] = round(median([s[k] for s in ss]), 1)
            verdicts: dict[str, int] = {}
            for s in ss:
                verdicts[s["verdict"]] = verdicts.get(s["verdict"], 0) + 1
            cell["verdicts"] = verdicts
            stage_rows.append(
                [
                    c["page"],
                    c["action"],
                    c["arm"],
                    cell["capture_before_p50"],
                    cell["dispatch_p50"],
                    cell["settle_p50"],
                    cell["capture_after_p50"],
                    cell["diff_p50"],
                    cell["p50_ms"],
                    ", ".join(f"{k}:{v}" for k, v in sorted(verdicts.items())),
                ]
            )
        out_cells.append(cell)
    # wide table: page | action | raw p50/p90 | receipt p50/p90 | noshot p50/p90 | overhead (receipt - raw, p50)
    by = {(c["page"], c["action"]): {} for c in out_cells}
    for c in out_cells:
        by[(c["page"], c["action"])][c["arm"]] = c
    for (page, action), arms in by.items():
        r = arms.get("raw", {})
        a = arms.get("receipt", {})
        b = arms.get("receipt_noshot", {})

        def f(c):
            return f"{c.get('p50_ms', '-')} / {c.get('p90_ms', '-')}" + (
                f" ({c['errors']} err)" if c.get("errors") else ""
            )

        ov = (
            (a.get("p50_ms") or 0) - (r.get("p50_ms") or 0)
            if a.get("p50_ms") and r.get("p50_ms") is not None
            else None
        )
        rows.append([page, action, r.get("n", 0), f(r), f(a), f(b), f"+{ov:.0f}" if ov is not None else "-"])
    md = [
        "# Overhead: receipt vs raw Playwright",
        "",
        f"n = {n} timed actions per cell after {warmup} warm-up actions; wall time in ms (p50 / p90). Every non-local request is aborted; the real pages are static copies (`bench/fixtures/real/SOURCES.md`).",
        "",
        md_table(
            [
                "page",
                "action",
                "n",
                "raw p50 / p90",
                "receipt p50 / p90",
                "receipt (no screenshot) p50 / p90",
                "receipt − raw (p50)",
            ],
            rows,
        ),
        "",
        "## Stage breakdown (receipt arms, p50 ms, from `Receipt.timing`)",
        "",
        md_table(
            [
                "page",
                "action",
                "arm",
                "capture_before",
                "dispatch",
                "settle",
                "capture_after",
                "diff+verdict",
                "total",
                "verdicts",
            ],
            stage_rows,
        ),
        "",
        "`diff+verdict` = total − (capture_before + dispatch + settle + capture_after). `settle` includes the mandatory 100 ms quiet window.",
        "",
    ]
    return {"cells": out_cells}, "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=3)
    args = ap.parse_args(argv)
    data = asyncio.run(run(args.n, args.warmup))
    summary, md = summarize(data, args.n, args.warmup)
    info = env_info({"chromium": data["chromium"], "n": args.n, "warmup": args.warmup})
    md += "\n## Environment\n\n" + env_md(info) + "\n"
    jp, mp = write_results("overhead", {"env": info, "summary": summary, "samples": data["samples"]}, md)
    log(f"wrote {jp} and {mp}")
    print(md)


if __name__ == "__main__":
    main()
