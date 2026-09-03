"""Fixture self-test for the hard task set: every task must be solvable by a scripted solution
(validator passes) and, where the trap has a naive path, the naive path must leave the validator
failing. Uses plain Playwright against the fixture server - no receipt, no model.

    python -m bench.tasks.selftest
"""

from __future__ import annotations

import asyncio
import sys

from .._common import FixtureServer
from . import HARD


async def _states(ctx):
    out = {}
    for p in ctx.pages:
        try:
            out[p.url] = await p.evaluate("window.__state ? window.__state() : null")
        except Exception:
            out[p.url] = None
    return out


# (task id, naive steps, solving steps). Steps are (op, selector, value) executed by Playwright.
async def do(page, ctx, steps):
    for st in steps:
        op = st[0]
        if op == "click":
            await page.click(st[1], timeout=2500)
        elif op == "click_nowait":
            try:
                await page.click(st[1], timeout=1500, force=st[2] if len(st) > 2 else False)
            except Exception:
                pass
        elif op == "fill":
            await page.fill(st[1], st[2])
        elif op == "type":
            await page.type(st[1], st[2])
        elif op == "press":
            await page.press(st[1], st[2])
        elif op == "select":
            await page.select_option(st[1], st[2])
        elif op == "scroll":
            await page.eval_on_selector(st[1], f"el => el.scrollTop += {st[2]}")
        elif op == "wait":
            await page.wait_for_timeout(st[1])
        elif op == "newtab":
            async with ctx.expect_page() as np:
                await page.click(st[1])
            page = await np.value
            await page.wait_for_load_state()
        elif op == "check":
            await page.check(st[1])
    return page


SOL = {
    "inbox_star_deep": (
        [("click_nowait", "#star-30")],
        [("scroll", "#list", 700), ("wait", 100), ("click", "#star-30")],
    ),
    "inbox_archive_deep": (
        [("click_nowait", "#sel-38")],
        [("scroll", "#list", 1500), ("wait", 100), ("check", "#sel-38"), ("click", "#archive-selected")],
    ),
    "inbox_menu_delete": ([("click_nowait", "#delete-5")], [("click", "#menu-5"), ("click", "#delete-5")]),
    "inbox_modal_star": ([("click_nowait", "#star-3")], [("click", "#close-whatsnew"), ("click", "#star-3")]),
    "inbox_bulk_archive": (
        [("click_nowait", "#archive-selected")],
        [("check", "#sel-6"), ("check", "#sel-7"), ("click", "#archive-selected")],
    ),
    "inbox_confirm_delete": (
        [("click", "#menu-8"), ("click", "#delete-8")],
        [("click", "#menu-8"), ("click", "#delete-8"), ("click", "#confirm-del")],
    ),
    "inbox_modal_bulk": (
        [("click_nowait", "#sel-6"), ("click_nowait", "#archive-selected")],
        [("click", "#close-whatsnew"), ("check", "#sel-6"), ("click", "#archive-selected")],
    ),
    "profile_save_phone": (
        [("fill", "#display", "Ada"), ("click", "#save"), ("wait", 1200)],
        [("fill", "#display", "Ada"), ("fill", "#phone", "555"), ("click", "#save"), ("wait", 1200)],
    ),
    "profile_native": (
        [("fill", "#display", "Ada"), ("click", "#save"), ("wait", 1200)],
        [("fill", "#display", "Ada"), ("fill", "#phone", "555"), ("click", "#save"), ("wait", 1200)],
    ),
    "profile_rename_optimistic": (
        [("fill", "#project-name", "Phoenix"), ("click", "#rename"), ("wait", 1200)],
        [
            ("fill", "#project-name", "Phoenix"),
            ("click", "#rename"),
            ("wait", 1200),
            ("click", "#rename"),
            ("wait", 1200),
        ],
    ),
    "profile_2fa": ([("check", "#twofa")], [("check", "#twofa"), ("click", "#apply-2fa")]),
    "profile_beta": (
        [("click_nowait", "#beta"), ("click", "#save"), ("wait", 1200)],
        [("click", "summary"), ("check", "#beta"), ("click", "#save"), ("wait", 1200)],
    ),
    "profile_lang": (
        [("select", "#lang", "de")],
        [("select", "#lang", "de"), ("click", "#save"), ("wait", 1200)],
    ),
    "profile_phone_beta": (
        [("click", "summary"), ("check", "#beta"), ("click", "#save"), ("wait", 1200)],
        [
            ("click", "summary"),
            ("check", "#beta"),
            ("fill", "#phone", "555"),
            ("click", "#save"),
            ("wait", 1200),
        ],
    ),
    "notes_autosave_title": (
        [("fill", "#title", "Q3 plan")],
        [("fill", "#title", "Q3 plan"), ("wait", 3200)],
    ),
    "notes_autosave_body": (
        [("fill", "#body", "Budget approved")],
        [("fill", "#body", "Budget approved"), ("wait", 3200)],
    ),
    "notes_publish_newtab": (
        [("click", "#publish")],
        [("newtab", "#publish"), ("click", "#confirm-publish")],
    ),
    "orders_ship_page3": (
        [("click_nowait", "#ship-1018")],
        [("click", "#next"), ("click", "#next"), ("click", "#ship-1018")],
    ),
    "orders_filter_enter": (
        [("fill", "#filter", "Okafor"), ("click_nowait", "#ship-1019")],
        [("fill", "#filter", "Okafor"), ("press", "#filter", "Enter"), ("click", "#ship-1019")],
    ),
    "orders_export_delay": (
        [("click", "#prepare"), ("click_nowait", "#export")],
        [("click", "#prepare"), ("wait", 2700), ("click", "#export")],
    ),
    "orders_infinite": (
        [("click_nowait", "#ship-1021")],
        [
            ("scroll", "#orders-box", 5000),
            ("wait", 600),
            ("scroll", "#orders-box", 5000),
            ("wait", 600),
            ("click", "#ship-1021"),
        ],
    ),
    "orders_ship_two_pages": (
        [("click", "#ship-1003"), ("click_nowait", "#ship-1026")],
        [
            ("click", "#ship-1003"),
            ("click", "#next"),
            ("click", "#next"),
            ("click", "#next"),
            ("click", "#ship-1026"),
        ],
    ),
    "cart_consent_checkout": (
        [("click_nowait", "#add-gadget"), ("click_nowait", "#checkout")],
        [("click", "#consent-accept"), ("click", "#add-gadget"), ("click", "#checkout")],
    ),
    "cart_stepper": (
        [("click", "#add-widget"), ("click_nowait", "#qty-widget"), ("click", "#checkout")],
        [
            ("click", "#add-widget"),
            ("click", "#inc-widget"),
            ("click", "#inc-widget"),
            ("click", "#checkout"),
        ],
    ),
    "cart_address_consent": (
        [("click_nowait", "#add-widget"), ("click_nowait", "#checkout")],
        [
            ("click", "#consent-accept"),
            ("click", "#add-widget"),
            ("fill", "#address", "1 Main St"),
            ("click", "#checkout"),
        ],
    ),
    "todo_modal_add": (
        [("click_nowait", "#new-todo"), ("click_nowait", "#add")],
        [("click", "#close-whatsnew"), ("fill", "#new-todo", "milk"), ("click", "#add")],
    ),
    "todo_confirm_delete": (
        [("click", "#item-2-del")],
        [("click", "#item-2-del"), ("click", "#confirm-del")],
    ),
    "todo_enter_add": (
        [("fill", "#new-todo", "eggs")],
        [("fill", "#new-todo", "eggs"), ("press", "#new-todo", "Enter")],
    ),
    "todo_banner_two": (
        [("click_nowait", "#new-todo"), ("click_nowait", "#add")],
        [
            ("click", "#accept-cookies"),
            ("fill", "#new-todo", "milk"),
            ("click", "#add"),
            ("fill", "#new-todo", "eggs"),
            ("click", "#add"),
        ],
    ),
    "wizard_noplan": (
        [("fill", "#name", "Ada"), ("click", "#next1"), ("click", "#next2"), ("click_nowait", "#finish")],
        [
            ("fill", "#name", "Ada"),
            ("click", "#next1"),
            ("check", "#plan-pro"),
            ("click", "#next2"),
            ("click", "#finish"),
        ],
    ),
    "wizard_delay_finish": (
        [("fill", "#name", "Ada"), ("click", "#next1"), ("click", "#next2"), ("click_nowait", "#finish")],
        [
            ("fill", "#name", "Ada"),
            ("click", "#next1"),
            ("click", "#next2"),
            ("wait", 2200),
            ("click", "#finish"),
        ],
    ),
    "wizard_terms_pro": (
        [
            ("fill", "#name", "Grace"),
            ("click", "#next1"),
            ("check", "#plan-pro"),
            ("click", "#next2"),
            ("click_nowait", "#finish"),
        ],
        [
            ("fill", "#name", "Grace"),
            ("click", "#next1"),
            ("check", "#plan-pro"),
            ("check", "#terms"),
            ("click", "#next2"),
            ("click", "#finish"),
        ],
    ),
    "settings_strict_dark": (
        [("check", "#dark"), ("click", "#save"), ("wait", 1500)],
        [("check", "#dark"), ("fill", "#email", "a@b.c"), ("click", "#save"), ("wait", 1500)],
    ),
    "settings_lang": (
        [("select", "#lang", "fr")],
        [("select", "#lang", "fr"), ("click", "#save"), ("wait", 1500)],
    ),
    "table_gate_sort": (
        [("click", "#col-score"), ("click", "#col-score"), ("click_nowait", "tr[data-name=Peggy] button")],
        [
            ("click", "#col-score"),
            ("click", "#col-score"),
            ("check", "tr[data-name=Peggy] input"),
            ("click", "tr[data-name=Peggy] button"),
        ],
    ),
    "search_slow_enter": (
        [("fill", "#q", "banana"), ("click_nowait", "#result-banana")],
        [("fill", "#q", "banana"), ("press", "#q", "Enter"), ("wait", 2800), ("click", "#result-banana")],
    ),
}


async def main():
    from playwright.async_api import async_playwright

    missing = [t["id"] for t in HARD if t["id"] not in SOL]
    assert not missing, f"no scripted solution for {missing}"
    bad = []
    async with FixtureServer() as srv, async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        for t in HARD:
            naive, solve = SOL[t["id"]]
            res = {}
            for label, steps in (("naive", naive), ("solve", solve)):
                ctx = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await ctx.new_page()
                await page.goto(srv.url(t["app"]))
                err = None
                try:
                    await do(page, ctx, steps)
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e).splitlines()[0][:80]}"
                ok = bool(t["check"](await _states(ctx)))
                res[label] = (ok, err)
                await ctx.close()
            flag = "" if (res["solve"][0] and not res["naive"][0]) else "  <-- PROBLEM"
            if flag:
                bad.append(t["id"])
            print(
                f"{t['id']:26s} naive={'PASS' if res['naive'][0] else 'fail'}{(' (' + res['naive'][1] + ')') if res['naive'][1] else ''}  solve={'PASS' if res['solve'][0] else 'FAIL'}{(' (' + res['solve'][1] + ')') if res['solve'][1] else ''}{flag}",
                flush=True,
            )
        await browser.close()
    print(
        f"\n{len(HARD) - len(bad)}/{len(HARD)} tasks: scripted solution passes and naive path fails"
        + (f"; PROBLEMS: {bad}" if bad else "")
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
