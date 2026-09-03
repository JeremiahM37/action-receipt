"""MCP server exposing the receipt-returning browser tools.

Run:  python -m action_receipt.server [--cdp ws://127.0.0.1:9222/devtools/browser/...] [--headed]

Every action tool returns ``{"result": ..., "receipt": {...}}``. ``receipt_begin`` /
``receipt_end`` wrap an action performed by any other tool on the same browser (attach both
over CDP). A failure that prevents a receipt (no current tab, bad selector syntax, browser
gone) is returned as ``{"error": {"type": ..., "message": ...}, "receipt": None}`` - never as
a raised exception, so the caller always gets structured output.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import os
from typing import Any

from mcp.server.mcpserver import MCPServer

from .schema import RECEIPT_JSON_SCHEMA
from .session import ReceiptSession
from .settle import SettleConfig

server = MCPServer(
    "action-receipt",
    instructions=(
        "Browser tools that return a receipt for every action: dispatch -> settlement -> delta -> verdict. "
        "Read receipt.verdict before planning the next step: 'no_op' means the page did not change, "
        "'blocked' means the target could not receive the action, 'navigated' means the URL/tab changed, "
        "'unknown' means the page never went quiet or the tab is gone. receipt.hint says what to try instead. "
        "Pass frame=<iframe selector> to act inside a same-origin iframe."
    ),
)

_session: ReceiptSession | None = None
_opts: dict[str, Any] = {"cdp": None, "headless": True, "new_context": False}
_lock = asyncio.Lock()


async def _sess() -> ReceiptSession:
    global _session
    if _session is None:
        s = ReceiptSession(
            settle_cfg=SettleConfig(
                quiet_ms=float(os.environ.get("AR_QUIET_MS", "100")),
                timeout_ms=float(os.environ.get("AR_SETTLE_TIMEOUT_MS", "10000")),
                body_grace_ms=float(os.environ.get("AR_BODY_GRACE_MS", "1500")),
                timer_wait_ms=float(os.environ.get("AR_TIMER_WAIT_MS", "5000")),
                pre_settle_ms=float(os.environ.get("AR_PRE_SETTLE_MS", "800")),
            )
        )
        await s.start(cdp=_opts["cdp"], headless=_opts["headless"], new_context=_opts["new_context"])
        _session = s
    return _session


def _out(result: Any, receipt) -> dict[str, Any]:
    return {"result": result, "receipt": receipt.to_dict()}


def _structured(fn):
    """Turn any exception from a tool into a structured error result."""

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except Exception as e:  # the whole point is to never raise past here
            msg = str(e).splitlines()[0] if str(e) else ""
            # e.__class__, not type(e): the `type` tool below shadows the builtin in this module.
            return {"error": {"type": e.__class__.__name__, "message": msg[:300]}, "receipt": None}

    return wrapper


@server.tool()
@_structured
async def open(url: str) -> dict[str, Any]:
    """Open a new tab at URL and make it current. Returns a receipt for the load."""
    s = await _sess()
    async with _lock:
        page = await s.new_page()
        result, receipt = await s.navigate(url, page=page)
        return _out(result, receipt)


@server.tool()
@_structured
async def navigate(url: str) -> dict[str, Any]:
    """Navigate the current tab to URL."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.navigate(url)
        return _out(result, receipt)


@server.tool()
@_structured
async def click(
    selector: str, button: str = "left", click_count: int = 1, frame: str | None = None
) -> dict[str, Any]:
    """Click the first element matching a Playwright selector (CSS, text=..., role=button[name=...]).
    click_count=2 is a double-click. frame=<iframe selector> targets an element inside that iframe."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.click(selector, button=button, click_count=click_count, frame=frame)
        return _out(result, receipt)


@server.tool()
@_structured
async def hover(selector: str, frame: str | None = None) -> dict[str, Any]:
    """Move the pointer over the element (reveals hover menus). The receipt says whether anything appeared."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.hover(selector, frame=frame)
        return _out(result, receipt)


@server.tool()
@_structured
async def select(selector: str, value: str, frame: str | None = None) -> dict[str, Any]:
    """Choose an option of a <select> by value (falls back to label). The receipt carries value_before/after."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.select(selector, value, frame=frame)
        return _out(result, receipt)


@server.tool()
@_structured
async def type(
    selector: str, text: str, clear: bool = False, submit: bool = False, frame: str | None = None
) -> dict[str, Any]:
    """Type text into the element (key by key). clear=True replaces the value; submit=True presses Enter after."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.type(selector, text, clear=clear, submit=submit, frame=frame)
        return _out(result, receipt)


@server.tool()
@_structured
async def press(key: str, selector: str | None = None, frame: str | None = None) -> dict[str, Any]:
    """Press a key (e.g. Enter, Escape, Tab, Control+a) on the element or, without selector, on the page."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.press(key, selector=selector, frame=frame)
        return _out(result, receipt)


@server.tool()
@_structured
async def scroll(
    direction: str = "down", pages: float = 1.0, selector: str | None = None, frame: str | None = None
) -> dict[str, Any]:
    """Scroll by wheel gesture: `pages` viewport-heights in `direction` (up/down/left/right), over `selector` if given."""
    s = await _sess()
    async with _lock:
        result, receipt = await s.scroll(direction=direction, pages=pages, selector=selector, frame=frame)
        return _out(result, receipt)


@server.tool()
@_structured
async def snapshot(aria: bool = True) -> dict[str, Any]:
    """Current page state: url, title, scroll geometry, focus, modals, scrollable containers, frames, tabs, aria tree."""
    s = await _sess()
    async with _lock:
        return await s.snapshot(aria=aria)


@server.tool()
@_structured
async def tabs(select: int | None = None) -> dict[str, Any]:
    """List open tabs; pass select=<index> to make one current."""
    s = await _sess()
    async with _lock:
        pages = s.pages()
        if select is not None:
            if select < 0 or select >= len(pages):
                return {"error": {"type": "IndexError", "message": f"no tab {select}"}, "count": len(pages)}
            await s.use_page(pages[select])
        return {"tabs": [{"index": i, "url": p.url, "current": p == s.current} for i, p in enumerate(pages)]}


@server.tool()
@_structured
async def receipt_last(n: int = 1) -> dict[str, Any]:
    """Return the last n receipts (most recent last)."""
    s = await _sess()
    return {"receipts": [r.to_dict() for r in s.receipts[-n:]]}


@server.tool()
async def receipt_schema() -> dict[str, Any]:
    """JSON Schema of the receipt object every action tool returns."""
    return RECEIPT_JSON_SCHEMA


@server.tool()
@_structured
async def receipt_begin(
    label: str = "external",
    selector: str | None = None,
    page_url: str | None = None,
    frame: str | None = None,
) -> dict[str, Any]:
    """Wrap mode: capture the before-state. Perform the action with any other tool, then call receipt_end.
    page_url selects which tab to observe (substring match); default is the current tab."""
    s = await _sess()
    async with _lock:
        if page_url:
            for p in s.pages():
                if page_url in p.url:
                    await s.use_page(p)
                    break
            else:
                return {
                    "error": {"type": "LookupError", "message": f"no tab whose url contains {page_url!r}"},
                    "receipt": None,
                }
        elif s.current is None and s.pages():
            await s.use_page(s.pages()[-1])
        rid = await s.begin(label, selector=selector, frame=frame)
        return {"receipt_id": rid, "observing": s.current.url if s.current else None}


@server.tool()
@_structured
async def receipt_end() -> dict[str, Any]:
    """Wrap mode: settle, capture the after-state, and return the receipt for what happened since receipt_begin."""
    s = await _sess()
    async with _lock:
        receipt = await s.end()
        return {"receipt": receipt.to_dict()}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="action-receipt")
    ap.add_argument(
        "--cdp",
        default=os.environ.get("AR_CDP"),
        help="attach to a running Chromium over CDP (ws:// or http://host:port)",
    )
    ap.add_argument(
        "--cdp-new-context",
        action="store_true",
        help="with --cdp: use an isolated incognito context instead of the browser's default one "
        "(never touches existing tabs, but wrap mode cannot observe them)",
    )
    ap.add_argument("--headed", action="store_true", help="launch a visible Chromium instead of headless")
    ap.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http", "sse"])
    args = ap.parse_args(argv)
    _opts["cdp"] = args.cdp
    _opts["headless"] = not args.headed
    _opts["new_context"] = args.cdp_new_context
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
