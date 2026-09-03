"""Fetch the three heavy real pages ONCE and save them as static fixtures under
bench/fixtures/real/ (rendered DOM via Playwright, <script> tags stripped so the saved copy is
inert). The benchmarks never hit live sites: they load these files from the local fixture
server with every non-127.0.0.1 request aborted.

    python -m bench.fetch_fixtures

Writes real/<name>.html and real/SOURCES.md (URL, fetch date, byte size, element count).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from ._common import FIXTURES, log

REAL = FIXTURES / "real"
PAGES = {
    "wikipedia": "https://en.wikipedia.org/wiki/Web_browser",
    "mdn": "https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement",
    "github": "https://github.com/microsoft/playwright",
}


async def main() -> None:
    from playwright.async_api import async_playwright

    REAL.mkdir(parents=True, exist_ok=True)
    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36",
        )
        for name, url in PAGES.items():
            page = await ctx.new_page()
            log(f"fetch {name}: {url}")
            try:
                await page.goto(url, wait_until="load", timeout=60000)
                await asyncio.sleep(2.0)
                html = await page.content()
                count = await page.evaluate("document.querySelectorAll('*').length")
                title = await page.evaluate("document.title")
            except Exception as e:
                log(f"  FAILED: {e}")
                rows.append((name, url, "FAILED", 0, 0, ""))
                await page.close()
                continue
            await page.close()
            # strip scripts (inert static copy) and <base> tags; keep everything else verbatim
            html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
            html = re.sub(r"<base\b[^>]*>", "", html, flags=re.I)
            html = (
                f"<!-- saved from {url} on {datetime.now(UTC).date()} by bench/fetch_fixtures.py; scripts stripped -->\n"
                + html
            )
            out = REAL / f"{name}.html"
            out.write_text(html)
            rows.append(
                (name, url, datetime.now(UTC).isoformat(timespec="seconds"), len(html.encode()), count, title)
            )
            log(f"  saved {out.name}: {len(html)} chars, {count} elements, title={title!r}")
        await browser.close()
    md = [
        "# Real-page fixtures",
        "",
        "Fetched once, saved as inert static HTML (scripts stripped). The benchmarks load these from the",
        "local fixture server with all non-local requests aborted, so no live site is contacted in a loop.",
        "",
        "| name | source URL | fetched (UTC) | bytes | elements at fetch | title |",
        "|---|---|---|---:|---:|---|",
    ]
    for r in rows:
        md.append("| " + " | ".join(str(x) for x in r) + " |")
    (REAL / "SOURCES.md").write_text("\n".join(md) + "\n")
    log("wrote SOURCES.md")


if __name__ == "__main__":
    asyncio.run(main())
