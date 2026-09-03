"""Bench 5 - real-site smoke (opt-in, one pass, live network).

    python -m bench.realsite --live

Ten actions on five live public pages, once, to show verdicts on real DOMs. Without --live it
prints what it would do and exits 0. Records the pages and the date.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from ._common import env_info, env_md, log, md_table, write_results

# (page label, url, [(action, args)])
PLAN = [
    (
        "Wikipedia article",
        "https://en.wikipedia.org/wiki/Web_browser",
        [
            ("scroll", {"direction": "down", "pages": 1.0}),
            ("type", {"selector": "#searchInput, input[type=search]", "text": "Firefox"}),
        ],
    ),
    (
        "MDN reference page",
        "https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement",
        [
            ("scroll", {"direction": "down", "pages": 1.0}),
            ("click", {"selector": "a[href='/en-US/docs/Web/API']"}),
        ],
    ),
    (
        "Hacker News front page",
        "https://news.ycombinator.com/",
        [
            ("scroll", {"direction": "down", "pages": 1.0}),
            ("click", {"selector": "a.morelink"}),
        ],
    ),
    (
        "GitHub repo page",
        "https://github.com/microsoft/playwright",
        [
            ("scroll", {"direction": "down", "pages": 1.0}),
            ("click", {"selector": "a#issues-tab, a[href='/microsoft/playwright/issues']"}),
        ],
    ),
    (
        "httpbin HTML form",
        "https://httpbin.org/forms/post",
        [
            ("type", {"selector": "input[name=custname]", "text": "Ada"}),
            ("click", {"selector": "input[value=medium]"}),
        ],
    ),
]


async def run():
    from playwright.async_api import async_playwright

    from action_receipt.session import ReceiptSession
    from action_receipt.settle import SettleConfig

    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36",
        )
        sess = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=10000), screenshots=True)
        await sess.attach_context(ctx)
        for label, url, actions in PLAN:
            log(f"{label}: {url}")
            try:
                page = await sess.new_page(url)
            except Exception as e:
                rows.append(
                    {
                        "page": label,
                        "url": url,
                        "action": "open",
                        "error": f"{type(e).__name__}: {str(e)[:120]}",
                    }
                )
                continue
            await asyncio.sleep(1.0)
            elements = await page.evaluate("document.querySelectorAll('*').length")
            for action, args in actions:
                try:
                    if action == "scroll":
                        _, r = await sess.scroll(page=page, **args)
                    elif action == "click":
                        _, r = await sess.click(args["selector"], page=page)
                    elif action == "type":
                        _, r = await sess.type(args["selector"], args["text"], page=page)
                    rows.append(
                        {
                            "page": label,
                            "url": url,
                            "elements": elements,
                            "action": f"{action} {args}",
                            "verdict": r.verdict.verdict,
                            "evidence": r.verdict.evidence[:4],
                            "hint": r.verdict.hint,
                            "settled_by": r.settlement.settled_by,
                            "settle_ms": round(r.settlement.elapsed_ms),
                            "timed_out": r.settlement.timed_out,
                            "busy_at_timeout": r.settlement.busy_at_timeout,
                            "total_ms": round(r.timing["total"]),
                            "capture_ms": round(r.after.elapsed_ms),
                            "dispatch_error": r.dispatch.get("error"),
                        }
                    )
                    log(
                        f"  {action} -> {r.verdict.verdict} ({r.settlement.settled_by}, {r.settlement.elapsed_ms:.0f} ms)"
                    )
                except Exception as e:
                    rows.append(
                        {
                            "page": label,
                            "url": url,
                            "action": f"{action} {args}",
                            "error": f"{type(e).__name__}: {str(e)[:120]}",
                        }
                    )
            for p in list(sess.pages()):
                await p.close()
        chromium = browser.version
        await browser.close()
    return rows, chromium


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually hit the live pages (one pass)")
    args = ap.parse_args(argv)
    if not args.live:
        print("real-site smoke is opt-in: pass --live to run one pass over:")
        for label, url, actions in PLAN:
            print(f"  {label}: {url} ({len(actions)} actions)")
        return
    rows, chromium = asyncio.run(run())
    date = datetime.now(UTC).isoformat(timespec="seconds")
    table = []
    for r in rows:
        if "error" in r:
            table.append([r["page"], r["action"], "ERROR", r["error"], "", ""])
        else:
            table.append(
                [
                    r["page"] + f" ({r['elements']} el)",
                    r["action"],
                    r["verdict"],
                    "; ".join(r["evidence"])[:160],
                    f"{r['settled_by']} {r['settle_ms']} ms"
                    + (f" TIMEOUT busy={r['busy_at_timeout']}" if r["timed_out"] else ""),
                    f"{r['total_ms']} (capture {r['capture_ms']})",
                ]
            )
    md = [
        "# Real-site smoke (one pass, live)",
        "",
        f"Run at {date}. Pages and actions are listed in `bench/realsite.py`.",
        "",
        md_table(
            ["page", "action", "verdict", "evidence", "settled_by / settle ms", "receipt total ms"], table
        ),
        "",
    ]
    info = env_info({"chromium": chromium, "date_run": date})
    md.append("## Environment\n\n" + env_md(info) + "\n")
    jp, mp = write_results("realsite", {"env": info, "rows": rows}, "\n".join(md))
    log(f"wrote {jp} and {mp}")
    print("\n".join(md))


if __name__ == "__main__":
    main()
