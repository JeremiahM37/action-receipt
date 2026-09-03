"""Bench 2 - measured settlement vs fixed sleeps (the core claim).

    python -m bench.settlement [--trials 20] [--delays 0,50,100,250,500,1000,2000,3000]

bench/fixtures/settle.html lands the effect of a click after `delay` ms by one of four
mechanisms (query ?mode=): `timeout` (setTimeout only), `fetch` (network-bound), `transition`
(CSS transition, DOM write on transitionend), `twostage` (fetch, then a CSS spinner for delay/2,
then the DOM write). For every (mode, delay, policy) cell we click, wait per the policy, take
the same after-capture the receipt takes (``capture_state``), and record

  (a) stale: the capture does NOT contain the landed effect (#result still 'pending');
  (b) wait:  the time spent waiting before that capture.

Policies: blind sleeps 0.0 s (OSWorld run.py), 0.1 s (browser-use), 0.5 s (OSWorld's Claude
runner), 2.0 s (Anthropic computer-use reference); `receipt` (quiet_ms=100, the default) and
`receipt_q500` (quiet_ms=500). Modes run concurrently in separate browser contexts.
"""

from __future__ import annotations

import argparse
import asyncio
import time

from ._common import FixtureServer, env_info, env_md, log, md_table, mean, median, p90, write_results

MODES = ["timeout", "fetch", "transition", "twostage"]
DEFAULT_DELAYS = [0, 50, 100, 250, 500, 1000, 2000, 3000]
POLICIES = [
    ("blind_0.0", 0.0),
    ("blind_0.1", 0.1),
    ("blind_0.5", 0.5),
    ("blind_2.0", 2.0),
    ("receipt", None),
    ("receipt_q500", None),
]


def _landed(nodes: dict[str, str]) -> bool:
    for k, v in nodes.items():
        if k.endswith("span#result"):
            return "FINAL" in v
    return False


async def _trial(page, sess, sess500, url, policy, sleep_s):
    from action_receipt.capture import capture_state

    await page.goto(url, wait_until="load")
    if policy.startswith("receipt"):
        s = sess500 if policy == "receipt_q500" else sess
        _, r = await s.click("#go", page=page)
        return {
            "stale": not _landed(r.after.nodes),
            "wait_ms": r.timing["settle"],
            "total_ms": r.timing["total"],
            "verdict": r.verdict.verdict,
            "settled_by": r.settlement.settled_by,
            "timed_out": r.settlement.timed_out,
        }
    t0 = time.perf_counter()
    await page.locator("#go").click(timeout=3000)
    dispatch_ms = (time.perf_counter() - t0) * 1000
    await asyncio.sleep(sleep_s)
    cap = await capture_state(page, with_screenshot=True)
    return {
        "stale": not _landed(cap.nodes),
        "wait_ms": sleep_s * 1000,
        "total_ms": dispatch_ms + sleep_s * 1000 + cap.elapsed_ms,
    }


async def _run_mode(srv, mode, delays, trials, results):
    from playwright.async_api import async_playwright

    from action_receipt.session import ReceiptSession
    from action_receipt.settle import SettleConfig

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1000, "height": 700})
        sess = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=10000), screenshots=True)
        sess500 = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=500, timeout_ms=10000), screenshots=True)
        await sess.attach_context(ctx)
        await sess500.attach_context(ctx)
        page = await sess.new_page()
        await sess500.use_page(page)
        for delay in delays:
            url = srv.url(f"settle.html?mode={mode}&delay={delay}")
            for policy, sleep_s in POLICIES:
                cell = []
                for _ in range(trials):
                    try:
                        cell.append(await _trial(page, sess, sess500, url, policy, sleep_s))
                    except Exception as e:
                        cell.append(
                            {"stale": True, "wait_ms": None, "error": f"{type(e).__name__}: {str(e)[:100]}"}
                        )
                results[(mode, delay, policy)] = cell
                stale = sum(1 for c in cell if c["stale"])
                waits = [c["wait_ms"] for c in cell if c.get("wait_ms") is not None]
                log(
                    f"{mode:10s} delay={delay:5d} {policy:13s} stale={stale}/{len(cell)} wait_p50={median(waits) if waits else float('nan'):.0f}ms"
                )
        chromium = browser.version
        await browser.close()
    return chromium


async def run(delays, trials):
    results: dict = {}
    async with FixtureServer() as srv:
        versions = await asyncio.gather(*[_run_mode(srv, m, delays, trials, results) for m in MODES])
    return results, versions[0]


def summarize(results, delays, trials):
    policies = [p for p, _ in POLICIES]
    md = [
        "# Settlement vs fixed sleeps",
        "",
        f"{trials} trials per cell. Cell = **stale rate** (post-action capture missed the effect) / median wait before the capture, ms. "
        "Blind policies sleep a constant then capture; the receipt captures when its settlement says the page is quiet.",
        "",
    ]
    cells_out = []
    per_policy = {p: {"stale": 0, "n": 0, "waits": []} for p in policies}
    per_policy_mode = {}
    for mode in MODES:
        rows = []
        for delay in delays:
            row = [delay]
            for p in policies:
                cell = results.get((mode, delay, p), [])
                stale = sum(1 for c in cell if c["stale"])
                waits = [c["wait_ms"] for c in cell if c.get("wait_ms") is not None]
                errs = sum(1 for c in cell if "error" in c)
                w50 = median(waits) if waits else float("nan")
                row.append(
                    f"{100 * stale / len(cell):.0f}% / {w50:.0f}" + (f" ({errs} err)" if errs else "")
                    if cell
                    else "-"
                )
                cells_out.append(
                    {
                        "mode": mode,
                        "delay_ms": delay,
                        "policy": p,
                        "n": len(cell),
                        "stale": stale,
                        "stale_rate": stale / len(cell) if cell else None,
                        "wait_p50_ms": w50,
                        "wait_p90_ms": p90(waits) if waits else None,
                        "errors": errs,
                        "verdicts": {
                            v: sum(1 for c in cell if c.get("verdict") == v)
                            for v in {c.get("verdict") for c in cell if c.get("verdict")}
                        },
                        "settled_by": {
                            v: sum(1 for c in cell if c.get("settled_by") == v)
                            for v in {c.get("settled_by") for c in cell if c.get("settled_by")}
                        },
                    }
                )
                per_policy[p]["stale"] += stale
                per_policy[p]["n"] += len(cell)
                per_policy[p]["waits"] += waits
                pm = per_policy_mode.setdefault((mode, p), {"stale": 0, "n": 0, "waits": []})
                pm["stale"] += stale
                pm["n"] += len(cell)
                pm["waits"] += waits
            rows.append(row)
        md += [f"## mode = {mode}", "", md_table(["delay ms"] + policies, rows), ""]
    # headline: per policy, overall stale rate x mean wait
    rows = []
    for p in policies:
        d = per_policy[p]
        rows.append(
            [
                p,
                f"{100 * d['stale'] / d['n']:.1f}% ({d['stale']}/{d['n']})" if d["n"] else "-",
                f"{mean(d['waits']):.0f}" if d["waits"] else "-",
                f"{median(d['waits']):.0f}" if d["waits"] else "-",
                f"{p90(d['waits']):.0f}" if d["waits"] else "-",
            ]
        )
    md += [
        "## Headline: stale rate × wait time (all modes and delays pooled)",
        "",
        md_table(["policy", "stale rate", "mean wait ms", "median wait ms", "p90 wait ms"], rows),
        "",
    ]
    rows = []
    for mode in MODES:
        row = [mode]
        for p in policies:
            d = per_policy_mode[(mode, p)]
            row.append(
                f"{100 * d['stale'] / d['n']:.0f}% / {mean(d['waits']):.0f}" if d["n"] and d["waits"] else "-"
            )
        rows.append(row)
    md += [
        "## Per mode: stale rate / mean wait ms (delays pooled)",
        "",
        md_table(["mode"] + policies, rows),
        "",
    ]
    # receipt verdict/settled_by detail for stale receipt cells
    rows = []
    for c in cells_out:
        if c["policy"].startswith("receipt") and c["stale"]:
            rows.append(
                [
                    c["mode"],
                    c["delay_ms"],
                    c["policy"],
                    f"{c['stale']}/{c['n']}",
                    c["verdicts"],
                    c["settled_by"],
                ]
            )
    if rows:
        md += [
            "## Where the receipt was stale (what it reported)",
            "",
            md_table(["mode", "delay", "policy", "stale", "verdicts", "settled_by"], rows),
            "",
        ]
    return {
        "cells": cells_out,
        "per_policy": {
            p: {"stale": d["stale"], "n": d["n"], "mean_wait_ms": mean(d["waits"]) if d["waits"] else None}
            for p, d in per_policy.items()
        },
    }, "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--delays", default=",".join(str(d) for d in DEFAULT_DELAYS))
    args = ap.parse_args(argv)
    delays = [int(x) for x in args.delays.split(",")]
    results, chromium = asyncio.run(run(delays, args.trials))
    summary, md = summarize(results, delays, args.trials)
    info = env_info({"chromium": chromium, "trials": args.trials, "delays": delays})
    md += "\n## Environment\n\n" + env_md(info) + "\n"
    raw = {f"{m}|{d}|{p}": v for (m, d, p), v in results.items()}
    jp, mp = write_results("settlement", {"env": info, "summary": summary, "raw": raw}, md)
    log(f"wrote {jp} and {mp}")
    print(md)


if __name__ == "__main__":
    main()
