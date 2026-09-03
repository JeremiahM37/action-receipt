"""ReceiptSession - runs an action through dispatch -> settlement -> delta -> verdict.

Two modes:

* ``run(action, args, dispatch)``: the session performs the action itself (used by the MCP
  tools) - pre-flight target check, capture, dispatch, settle, capture, diff, decide.
* ``begin(...)`` / ``end()``: *wrap* mode - the caller performs the action with any other
  tool (playwright-mcp, raw CDP, a human) between the two calls; the session still produces a
  receipt because it shares the browser over CDP.

Every element-targeting action takes an optional ``frame`` - a selector for an ``<iframe>``
whose document holds ``selector``. Same-origin frames are fingerprinted and observed like the
top document; cross-origin frames can only be seen through the screenshot measure.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .capture import Capture, capture_state
from .delta import Delta, compute_delta
from .inject import INSTALL_JS, MARK_JS, PRESCROLL_JS, TARGET_JS
from .settle import (
    NavTracker,
    NetworkTracker,
    SettleConfig,
    SettleReport,
    ensure_installed,
    pre_settle,
    settle,
)
from .verdict import BLOCK_MARKERS, Verdict, decide

DEFAULT_ACTION_TIMEOUT_MS = 3000

# The dispatch anchor: marks every frame (animations already running and timers already pending
# become background) and returns the same aggregate the settle loop polls, so "mutations since
# dispatch" subtracts the same frames it later adds.
_PRE_DISPATCH_JS = MARK_JS
PRESCROLL_TIMEOUT_MS = 1000


def _describe_error(e: Exception) -> str:
    """First line of the error, plus any actionability reason Playwright put further down."""
    full = str(e)
    first = full.splitlines()[0] if full else ""
    low = full.lower()
    extra = [m for m in BLOCK_MARKERS if m in low and m not in first.lower()]
    return f"{type(e).__name__}: {first}" + (f" ({extra[0]})" if extra else "")


@dataclass
class Receipt:
    id: str
    action: str
    args: dict[str, Any]
    verdict: Verdict
    dispatch: dict[str, Any]
    settlement: SettleReport
    delta: Delta
    before: Capture
    after: Capture
    timing: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": {"name": self.action, "args": self.args},
            "verdict": self.verdict.verdict,
            "evidence": self.verdict.evidence,
            "hint": self.verdict.hint,
            "dispatch": self.dispatch,
            "settlement": self.settlement.to_dict(),
            "delta": self.delta.to_dict(),
            "before": self.before.summary(),
            "after": self.after.summary(),
            "timing_ms": {k: round(v, 1) for k, v in self.timing.items()},
        }


class PageState:
    def __init__(self, page: Page, context: BrowserContext):
        self.page = page
        self.net = NetworkTracker(page)
        self.nav = NavTracker(page, context)


class ReceiptSession:
    def __init__(
        self,
        *,
        settle_cfg: SettleConfig | None = None,
        screenshots: bool = True,
        action_timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
        dialog_action: str = "accept",
    ):
        self.settle_cfg = settle_cfg or SettleConfig()
        self.screenshots = screenshots
        self.action_timeout_ms = action_timeout_ms
        self.dialog_action = dialog_action
        self._pw: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._pages: dict[Page, PageState] = {}
        self.current: Page | None = None
        self.receipts: list[Receipt] = []
        self._pending: dict[str, Any] | None = None
        self._preflight: dict[str, Any] | None = (
            None  # target info from the before-capture, readable by dispatch closures
        )
        self.owns_browser = False
        self.owns_context = False

    # ---------------------------------------------------------------- lifecycle
    async def start(
        self,
        *,
        cdp: str | None = None,
        headless: bool = True,
        viewport=(1280, 800),
        new_context: bool = False,
    ) -> None:
        """Launch a Chromium, or attach to one over CDP.

        With ``cdp`` and ``new_context=False`` the session shares the browser's default context
        (needed for wrap mode around another tool's tabs); the observer init script is then
        installed into every page that context navigates from now on. ``new_context=True``
        opens an isolated incognito context instead and never touches existing tabs."""
        self._pw = await async_playwright().start()
        if cdp:
            self.browser = await self._pw.chromium.connect_over_cdp(cdp)
            self.owns_browser = False
            ctxs = self.browser.contexts
            if new_context or not ctxs:
                self.context = await self.browser.new_context(
                    viewport={"width": viewport[0], "height": viewport[1]}
                )
                self.owns_context = True
            else:
                self.context = ctxs[0]
        else:
            self.browser = await self._pw.chromium.launch(headless=headless)
            self.owns_browser = True
            self.context = await self.browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]}
            )
        await self.context.add_init_script(INSTALL_JS)
        for p in self.context.pages:
            self._track(p)

    async def attach_context(self, context: BrowserContext) -> None:
        """Use an already-created Playwright context (tests)."""
        self.context = context
        await context.add_init_script(INSTALL_JS)
        for p in context.pages:
            self._track(p)

    async def close(self) -> None:
        # In wrap/CDP mode we only close what we opened ourselves.
        if self.owns_browser and self.browser:
            await self.browser.close()
        elif self.owns_context and self.context:
            await self.context.close()
        if self._pw:
            await self._pw.stop()

    def _track(self, page: Page) -> PageState:
        st = self._pages.get(page)
        if st is None:
            assert self.context is not None, "start() or attach_context() first"
            st = PageState(page, self.context)
            st.nav.dialog_action = self.dialog_action
            self._pages[page] = st

            def _forget(p: Page) -> None:
                self._pages.pop(p, None)

            page.on("close", _forget)
        return st

    async def new_page(self, url: str | None = None) -> Page:
        assert self.context is not None, "start() or attach_context() first"
        page = await self.context.new_page()
        self._track(page)
        self.current = page
        if url:
            await page.goto(url, wait_until="load")
        await ensure_installed(page)
        return page

    async def use_page(self, page: Page) -> None:
        self._track(page)
        self.current = page
        await ensure_installed(page)

    def pages(self) -> list[Page]:
        return list(self.context.pages) if self.context else []

    def _state(self, page: Page | None = None) -> PageState:
        page = page or self.current
        if page is None:
            raise RuntimeError("no current page; call open() first")
        return self._track(page)

    @staticmethod
    def _locator(page: Page, selector: str, frame: str | None):
        """First match of ``selector``; inside the iframe matched by ``frame`` if given."""
        if frame:
            return page.frame_locator(frame).first.locator(selector).first
        return page.locator(selector).first

    async def _prescroll(self, target) -> dict[str, Any] | None:
        """Bring the target into view *before* the before-capture (bench finding F7). Playwright
        would scroll it during dispatch anyway; doing it first keeps the scroll - and every pixel
        it moves - out of the delta, while a real effect (a canvas paint) still shows."""
        try:
            handle = (
                await target.element_handle(timeout=PRESCROLL_TIMEOUT_MS)
                if hasattr(target, "element_handle")
                else target
            )
            if handle is None:
                return None
            info = await handle.evaluate(TARGET_JS)
            if not info or not info.get("visible") or info.get("offscreen"):
                return None
            out = await handle.evaluate(PRESCROLL_JS)
            return out if out and out.get("scrolled") else None
        except Exception as e:
            return {
                "scrolled": False,
                "error": f"{type(e).__name__}: {(str(e).splitlines() or [''])[0][:120]}",
            }

    # ---------------------------------------------------------------- core
    async def run(
        self,
        action: str,
        args: dict[str, Any],
        dispatch: Callable[[], Awaitable[Any]],
        *,
        page: Page | None = None,
        target=None,
        prescroll: bool = False,
    ) -> tuple[Any, Receipt]:
        """Run ``dispatch`` and produce a receipt for it. Never raises for page-level failures:
        a closed or crashed tab yields an ``unknown`` receipt whose evidence names the cause.
        With ``prescroll`` the target is scrolled into view before the before-capture."""
        st = self._state(page)
        page = st.page
        rid = uuid.uuid4().hex[:12]
        timing: dict[str, float] = {}
        t_start = time.perf_counter()

        await ensure_installed(page)
        # Order matters: scroll first (it may trigger lazy loading), then let the page go quiet
        # (or show its background cadence), then capture a quiet, in-view state.
        t_ps = time.perf_counter()
        prescrolled = await self._prescroll(target) if (prescroll and target is not None) else None
        timing["prescroll"] = (time.perf_counter() - t_ps) * 1000
        presettle = await pre_settle(page, self.settle_cfg)
        timing["pre_settle"] = presettle["elapsed_ms"]
        before = await capture_state(
            page, target=target, with_screenshot=self.screenshots, mark_background=True
        )
        timing["capture_before"] = before.elapsed_ms
        if prescrolled and before.target and "error" not in before.target:
            before.target["prescroll"] = prescrolled
        self._preflight = before.target
        if before.target and before.target.get("type") == "password" and "text" in args:
            args = {**args, "text": "*" * len(str(args["text"]))}  # never echo a secret in a receipt

        try:
            pre = await page.evaluate(_PRE_DISPATCH_JS) or {}
            page_t_dispatch, mutations_at_dispatch, bg_at_dispatch = (
                pre.get("now"),
                pre.get("mutations", 0),
                pre.get("bgMutations", 0),
            )
        except Exception:
            page_t_dispatch, mutations_at_dispatch, bg_at_dispatch = None, 0, 0
        t_dispatch = time.perf_counter()
        result: Any = None
        dispatch_error: str | None = None
        try:
            result = await dispatch()
        except Exception as e:
            dispatch_error = _describe_error(e)
        timing["dispatch"] = (time.perf_counter() - t_dispatch) * 1000

        report = await settle(
            page,
            st.net,
            st.nav,
            t_dispatch=t_dispatch,
            page_t_dispatch=page_t_dispatch,
            cfg=self.settle_cfg,
            mutations_at_dispatch=mutations_at_dispatch,
            bg_mutations_at_dispatch=bg_at_dispatch,
        )
        timing["settle"] = report.elapsed_ms

        after = await capture_state(page, target=target, with_screenshot=self.screenshots)
        timing["capture_after"] = after.elapsed_ms
        # The window in which an effect could have been seen: dispatch start -> after-capture end.
        timing["observed_window"] = (time.perf_counter() - t_dispatch) * 1000

        events = await self._events(st, t_dispatch)
        delta = compute_delta(before, after, events)
        verdict = decide(
            delta,
            action=action,
            args=args,
            dispatch_error=dispatch_error,
            settlement=report,
            before_scroll=before.scroll,
            containers=before.scrollable_containers,
            target_before=before.target,
            frames=before.frames,
        )
        timing["total"] = (time.perf_counter() - t_start) * 1000
        receipt = Receipt(
            id=rid,
            action=action,
            args=args,
            verdict=verdict,
            dispatch={
                "ok": dispatch_error is None,
                "error": dispatch_error,
                "elapsed_ms": round(timing["dispatch"], 1),
                "preflight": before.target,
                "pre_settle": presettle,
            },
            settlement=report,
            delta=delta,
            before=before,
            after=after,
            timing=timing,
        )
        self.receipts.append(receipt)
        return result, receipt

    async def _events(self, st: PageState, t: float) -> dict[str, Any]:
        events = st.nav.since(t)
        new_tab_urls = []
        for p in events["new_pages"]:
            try:
                await p.wait_for_load_state("domcontentloaded", timeout=2000)
            except Exception:
                pass
            new_tab_urls.append(p.url)
            self._track(p)
        events["new_tab_urls"] = new_tab_urls
        events["http_errors"] = st.net.http_errors_since(t)
        return events

    # ---------------------------------------------------------------- wrap mode
    async def begin(
        self,
        label: str = "external",
        *,
        page: Page | None = None,
        selector: str | None = None,
        frame: str | None = None,
    ) -> str:
        st = self._state(page)
        target = self._locator(st.page, selector, frame) if selector else None
        await ensure_installed(st.page)
        presettle = await pre_settle(st.page, self.settle_cfg)
        before = await capture_state(
            st.page, target=target, with_screenshot=self.screenshots, mark_background=True
        )
        try:
            pre = await st.page.evaluate(_PRE_DISPATCH_JS) or {}
            page_t, mut0, bg0 = pre.get("now"), pre.get("mutations", 0), pre.get("bgMutations", 0)
        except Exception:
            page_t, mut0, bg0 = None, 0, 0
        self._pending = {
            "id": uuid.uuid4().hex[:12],
            "label": label,
            "page": st.page,
            "target": target,
            "before": before,
            "t": time.perf_counter(),
            "page_t": page_t,
            "mut0": mut0,
            "bg0": bg0,
            "selector": selector,
            "frame": frame,
            "pre_settle": presettle,
        }
        return self._pending["id"]

    async def end(self) -> Receipt:
        if not self._pending:
            raise RuntimeError("receipt_end without receipt_begin")
        p = self._pending
        self._pending = None
        st = self._state(p["page"])
        report = await settle(
            st.page,
            st.net,
            st.nav,
            t_dispatch=p["t"],
            page_t_dispatch=p["page_t"],
            cfg=self.settle_cfg,
            mutations_at_dispatch=p["mut0"],
            bg_mutations_at_dispatch=p["bg0"],
        )
        after = await capture_state(st.page, target=p["target"], with_screenshot=self.screenshots)
        observed = (time.perf_counter() - p["t"]) * 1000
        events = await self._events(st, p["t"])
        delta = compute_delta(p["before"], after, events)
        args = {"selector": p["selector"], "frame": p["frame"], "mode": "wrap"}
        verdict = decide(
            delta,
            action=p["label"],
            args=args,
            dispatch_error=None,
            settlement=report,
            before_scroll=p["before"].scroll,
            containers=p["before"].scrollable_containers,
            target_before=p["before"].target,
            frames=p["before"].frames,
        )
        receipt = Receipt(
            id=p["id"],
            action=p["label"],
            args=args,
            verdict=verdict,
            dispatch={
                "ok": None,
                "error": None,
                "elapsed_ms": None,
                "preflight": p["before"].target,
                "mode": "wrap",
                "pre_settle": p["pre_settle"],
            },
            settlement=report,
            delta=delta,
            before=p["before"],
            after=after,
            timing={
                "pre_settle": p["pre_settle"]["elapsed_ms"],
                "capture_before": p["before"].elapsed_ms,
                "settle": report.elapsed_ms,
                "capture_after": after.elapsed_ms,
                "observed_window": observed,
                "total": (time.perf_counter() - p["t"]) * 1000,
            },
        )
        self.receipts.append(receipt)
        return receipt

    def _dispatch_timeout(self) -> int:
        """Full actionability wait normally; a short one when pre-flight already saw the target
        disabled / covered / invisible, so a blocked verdict does not cost the whole timeout."""
        t = self._preflight or {}
        if (
            t
            and "error" not in t
            and (
                t.get("disabled")
                or t.get("coveredBy")
                or t.get("visible") is False
                or t.get("readonly")
                or t.get("pointerEvents") == "none"
                or t.get("offscreen")
            )
        ):
            return min(self.action_timeout_ms, 300)
        return self.action_timeout_ms

    # ---------------------------------------------------------------- actions
    async def navigate(self, url: str, *, page: Page | None = None) -> tuple[Any, Receipt]:
        st = self._state(page)

        async def go():
            resp = await st.page.goto(url, wait_until="commit", timeout=self.action_timeout_ms * 10)
            return {"status": resp.status if resp else None}

        return await self.run("navigate", {"url": url}, go, page=st.page)

    async def click(
        self,
        selector: str,
        *,
        page: Page | None = None,
        button: str = "left",
        click_count: int = 1,
        frame: str | None = None,
    ) -> tuple[Any, Receipt]:
        st = self._state(page)
        loc = self._locator(st.page, selector, frame)

        async def do():
            await loc.click(timeout=self._dispatch_timeout(), button=button, click_count=click_count)
            return {"clicked": selector}

        return await self.run(
            "click",
            {"selector": selector, "button": button, "click_count": click_count, "frame": frame},
            do,
            page=st.page,
            target=loc,
            prescroll=True,
        )

    async def hover(
        self, selector: str, *, page: Page | None = None, frame: str | None = None
    ) -> tuple[Any, Receipt]:
        st = self._state(page)
        loc = self._locator(st.page, selector, frame)

        async def do():
            await loc.hover(timeout=self._dispatch_timeout())
            return {"hovered": selector}

        return await self.run(
            "hover", {"selector": selector, "frame": frame}, do, page=st.page, target=loc, prescroll=True
        )

    async def select(
        self, selector: str, value: str, *, page: Page | None = None, frame: str | None = None
    ) -> tuple[Any, Receipt]:
        """Choose an option of a ``<select>`` by value or label."""
        st = self._state(page)
        loc = self._locator(st.page, selector, frame)

        async def do():
            timeout = self._dispatch_timeout()
            # Check the options first: a missing option is a cheap, definite 'blocked', not a 3 s timeout.
            how = await loc.evaluate(
                "(el, v) => { if (el.tagName !== 'SELECT') return 'not-select'; const o = [...el.options];"
                " if (o.some(x => x.value === v)) return 'value'; if (o.some(x => x.label === v || x.text === v)) return 'label';"
                " return 'missing'; }",
                value,
                timeout=timeout,
            )
            if how == "missing":
                raise RuntimeError(f"did not find some options: {value!r} is not an option of {selector}")
            if how == "label":
                chosen = await loc.select_option(label=value, timeout=timeout)
            else:
                chosen = await loc.select_option(value=value, timeout=timeout)
            return {"selected": chosen}

        return await self.run(
            "select",
            {"selector": selector, "value": value, "frame": frame},
            do,
            page=st.page,
            target=loc,
            prescroll=True,
        )

    async def type(
        self,
        selector: str,
        text: str,
        *,
        page: Page | None = None,
        clear: bool = False,
        submit: bool = False,
        frame: str | None = None,
    ) -> tuple[Any, Receipt]:
        st = self._state(page)
        loc = self._locator(st.page, selector, frame)

        async def do():
            timeout = self._dispatch_timeout()
            if clear:
                await loc.fill(text, timeout=timeout)
            else:
                await loc.press_sequentially(text, timeout=timeout)
            if submit:
                await loc.press("Enter", timeout=timeout)
            return {"typed": len(text)}

        return await self.run(
            "type",
            {"selector": selector, "text": text, "clear": clear, "submit": submit, "frame": frame},
            do,
            page=st.page,
            target=loc,
            prescroll=True,
        )

    async def press(
        self, key: str, *, selector: str | None = None, page: Page | None = None, frame: str | None = None
    ) -> tuple[Any, Receipt]:
        st = self._state(page)
        loc = self._locator(st.page, selector, frame) if selector else None

        async def do():
            if loc is not None:
                await loc.press(key, timeout=self.action_timeout_ms)
            else:
                await st.page.keyboard.press(key)
            return {"pressed": key}

        return await self.run(
            "press",
            {"key": key, "selector": selector, "frame": frame},
            do,
            page=st.page,
            target=loc,
            prescroll=loc is not None,
        )

    async def scroll(
        self,
        *,
        direction: str = "down",
        pages: float = 1.0,
        selector: str | None = None,
        page: Page | None = None,
        frame: str | None = None,
    ) -> tuple[Any, Receipt]:
        """Scroll with a real wheel gesture (like browser-use), so a non-scrollable page yields a no_op."""
        st = self._state(page)
        loc = self._locator(st.page, selector, frame) if selector else None

        async def do():
            vp = st.page.viewport_size or {"width": 1280, "height": 800}
            amount = int(pages * vp["height"])
            if direction == "up":
                dy, dx = -amount, 0
            elif direction == "down":
                dy, dx = amount, 0
            elif direction == "left":
                dy, dx = 0, -amount
            else:
                dy, dx = 0, amount
            if loc is not None:
                box = await loc.bounding_box(timeout=self.action_timeout_ms)
                if box is None:
                    raise RuntimeError("element is not visible")
                await st.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            else:
                await st.page.mouse.move(vp["width"] / 2, vp["height"] / 2)
            await st.page.mouse.wheel(dx, dy)
            return {"wheel": {"dx": dx, "dy": dy}}

        return await self.run(
            "scroll",
            {"direction": direction, "pages": pages, "selector": selector, "frame": frame},
            do,
            page=st.page,
            target=loc,
        )

    async def snapshot(self, *, page: Page | None = None, aria: bool = True) -> dict[str, Any]:
        st = self._state(page)
        await ensure_installed(st.page)
        cap = await capture_state(st.page, with_screenshot=False)
        out = cap.summary()
        out["scrollable_containers"] = cap.scrollable_containers
        out["pages"] = [
            {"index": i, "url": p.url, "current": p == st.page} for i, p in enumerate(self.pages())
        ]
        if aria:
            try:
                out["aria"] = await st.page.locator("body").aria_snapshot(timeout=self.action_timeout_ms)
            except Exception as e:
                out["aria"] = f"unavailable: {type(e).__name__}"
        return out
