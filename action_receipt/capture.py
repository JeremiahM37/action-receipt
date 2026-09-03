"""Pre/post state capture and the two image measures (dHash + changed-pixel fraction)."""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageDraw

from .inject import CAPTURE_JS, TARGET_JS

# Image comparison is done at this width; the height follows the viewport aspect ratio.
_DIFF_WIDTH = 160
# A grayscale pixel counts as changed if it moved by more than this (0-255).
_PIXEL_DELTA = 24


@dataclass
class Screenshot:
    dhash: int
    width: int
    height: int
    small: Image.Image = field(repr=False)  # grayscale, _DIFF_WIDTH wide - kept for the pixel diff


@dataclass
class Capture:
    ok: bool
    ts: float
    elapsed_ms: float
    url: str = ""
    title: str = ""
    ready_state: str = ""
    viewport: dict[str, int] = field(default_factory=dict)
    scroll: dict[str, Any] = field(default_factory=dict)
    focused: dict[str, Any] | None = None
    modals: list[str] = field(default_factory=list)
    scrollable_containers: list[dict[str, Any]] = field(default_factory=list)
    frames: list[dict[str, Any]] = field(default_factory=list)  # same-origin iframes: path + scroll geometry
    element_count: int = 0  # elements in the fingerprint
    element_total: int = 0  # elements walked (== element_count unless truncated)
    truncated: bool = False
    text_hash: int = 0
    text_length: int = 0
    nodes: dict[str, str] = field(default_factory=dict, repr=False)  # path -> signature
    background: list[str] = field(
        default_factory=list
    )  # roots marked as already-mutating before the action (<= 50)
    background_paths: set[str] = field(
        default_factory=set, repr=False
    )  # every fingerprinted node under a background root
    background_rects: list[list[int]] = field(
        default_factory=list, repr=False
    )  # viewport [x0,y0,x1,y1] of top-document background roots
    validation: list[dict[str, Any]] = field(
        default_factory=list
    )  # controls constraint validation rejected since the dispatch anchor (<= 64)
    dom_hash: str = ""
    target: dict[str, Any] | None = None
    screenshot: Screenshot | None = field(default=None, repr=False)
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        """The part of a capture that goes into the receipt (no node list, no image)."""
        return {
            "ok": self.ok,
            "url": self.url,
            "title": self.title,
            "ready_state": self.ready_state,
            "scroll": self.scroll,
            "focused": self.focused,
            "modals": self.modals,
            "element_count": self.element_count,
            "element_total": self.element_total,
            "truncated": self.truncated,
            "frames": self.frames,
            "background": self.background,
            "dom_hash": self.dom_hash,
            "text_hash": self.text_hash,
            "screenshot_dhash": f"{self.screenshot.dhash:016x}" if self.screenshot else None,
            "target": self.target,
            "capture_ms": round(self.elapsed_ms, 1),
            "error": self.error,
        }


def dhash(img: Image.Image) -> int:
    """Classic 64-bit difference hash: 9x8 grayscale, compare horizontal neighbours."""
    g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.tobytes())  # 72 bytes, row-major
    bits = 0
    for row in range(8):
        for col in range(8):
            left = px[row * 9 + col]
            right = px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def changed_fraction(
    a: Image.Image,
    b: Image.Image,
    *,
    mask_rects: list[list[int]] | None = None,
    viewport: tuple[int, int] | None = None,
) -> float:
    """Fraction of pixels (at reduced resolution) whose gray level moved by > _PIXEL_DELTA.

    ``mask_rects`` are viewport-pixel rectangles ``[x0, y0, x1, y1]`` (the background roots'
    boxes) whose pixels are set aside - a ticker or a rAF-animated element must not read as
    the action's visual effect. ``viewport`` is the full-size (w, h) the rects refer to."""
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    diff = ImageChops.difference(a, b).point(lambda v: 255 if v > _PIXEL_DELTA else 0)
    if mask_rects and viewport and viewport[0] and viewport[1]:
        sx, sy = a.size[0] / viewport[0], a.size[1] / viewport[1]
        draw = ImageDraw.Draw(diff)
        for x0, y0, x1, y1 in mask_rects:
            draw.rectangle([int(x0 * sx), int(y0 * sy), int(x1 * sx) + 1, int(y1 * sy) + 1], fill=0)
    hist = diff.histogram()
    changed = hist[255] if len(hist) > 255 else 0
    total = a.size[0] * a.size[1]
    return changed / total if total else 0.0


def screenshot_from_png(data: bytes) -> Screenshot:
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    small_h = max(1, round(h * _DIFF_WIDTH / w)) if w else 1
    small = img.convert("L").resize((_DIFF_WIDTH, small_h), Image.Resampling.BILINEAR)
    return Screenshot(dhash=dhash(img), width=w, height=h, small=small)


DEFAULT_MAX_NODES = 20000

# Elements mutated within this many ms before the action are background (a ticker, a live feed).
from .settle import BACKGROUND_WINDOW_MS  # noqa: E402  (settle does not import capture)


async def capture_state(
    page,
    *,
    target=None,
    with_screenshot: bool = True,
    max_nodes: int = DEFAULT_MAX_NODES,
    mark_background: bool = False,
) -> Capture:
    """Capture the page state. ``target`` is an optional Playwright Locator/ElementHandle (it
    may live in a child frame; its probe runs in that frame's own context). With
    ``mark_background`` (the before-capture) elements that mutated within the last
    ``BACKGROUND_WINDOW_MS`` are marked so the delta and settlement can set them aside."""
    t0 = time.perf_counter()
    cap = Capture(ok=False, ts=time.time(), elapsed_ms=0.0)
    try:
        raw = await page.evaluate(
            CAPTURE_JS,
            {"maxNodes": max_nodes, "markBackground": mark_background, "bgWindowMs": BACKGROUND_WINDOW_MS},
        )
    except Exception as e:  # execution context destroyed, page closed, ...
        cap.error = f"capture failed: {type(e).__name__}: {e}"
        cap.elapsed_ms = (time.perf_counter() - t0) * 1000
        return cap
    cap.url = raw["url"]
    cap.title = raw["title"]
    cap.ready_state = raw["readyState"]
    cap.viewport = raw["viewport"]
    cap.scroll = raw["scroll"]
    cap.focused = raw["focused"]
    cap.modals = raw["modals"]
    cap.scrollable_containers = raw["scrollableContainers"]
    cap.frames = raw.get("frames", [])
    cap.background = raw.get("background", [])
    cap.background_rects = [[int(v) for v in r] for r in raw.get("backgroundRects", [])]
    cap.validation = raw.get("validation", [])
    cap.element_count = raw["elementCount"]
    cap.element_total = raw.get("elementTotal", raw["elementCount"])
    cap.truncated = raw["truncated"]
    cap.text_hash = raw["textHash"]
    cap.text_length = raw["textLength"]
    nodes: dict[str, str] = {}
    h = hashlib.sha1()
    blob = raw["nodes"]
    if blob:
        bg = cap.background_paths
        for line in blob.split("\n"):
            path, _, rest = line.partition("\t")
            sig, _, flag = rest.partition("\t")
            if flag:
                bg.add(path)
            nodes[path] = sig
            h.update(path.encode())
            h.update(b"\0")
            h.update(sig.encode())
            h.update(b"\n")
    cap.nodes = nodes
    cap.dom_hash = h.hexdigest()[:16]
    if target is not None:
        try:
            handle = target
            if hasattr(target, "element_handle"):
                handle = await target.element_handle(timeout=1000)
            # handle.evaluate runs in the handle's own execution context, so a target inside a
            # same-origin iframe is probed there (page.evaluate would reject a foreign handle).
            cap.target = await handle.evaluate(TARGET_JS) if handle is not None else None
        except Exception as e:
            cap.target = {"error": f"{type(e).__name__}: {e}"}
    if with_screenshot:
        try:
            png = await page.screenshot(type="png", animations="allow", caret="initial", timeout=5000)
            cap.screenshot = screenshot_from_png(png)
        except Exception as e:
            cap.error = f"screenshot failed: {type(e).__name__}: {e}"
    cap.ok = True
    cap.elapsed_ms = (time.perf_counter() - t0) * 1000
    return cap
