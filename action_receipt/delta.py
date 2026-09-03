"""Deterministic diff of two captures plus the events observed in between."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .capture import Capture, changed_fraction, hamming

MAX_LISTED_NODES = 12
# Below this fraction of changed pixels a screenshot is treated as unchanged (cursor blink,
# sub-pixel AA). Measured on the fixture pages: a real change is >= 0.01; noise is < 0.001.
SCREENSHOT_CHANGED_THRESHOLD = 0.002


@dataclass
class Delta:
    url_changed: bool = False
    url_before: str = ""
    url_after: str = ""
    url_only_fragment: bool = False
    title_changed: bool = False
    scroll_dx: int = 0
    scroll_dy: int = 0
    container_scroll: dict[str, Any] | None = None  # target's scrollable ancestor before/after
    frame_scrolls: list[dict[str, Any]] = field(
        default_factory=list
    )  # same-origin iframes whose window scrolled
    focus_changed: bool = False
    focus_before: str | None = None
    focus_after: str | None = None
    dom_changed: bool = False
    element_count_delta: int = 0
    text_changed: bool = False
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    nodes_modified: list[dict[str, str]] = field(default_factory=list)
    nodes_changed_total: int = 0
    nodes_background: list[str] = field(default_factory=list)  # changed nodes set aside as background (<= 12)
    nodes_background_total: int = 0
    background_roots: list[str] = field(
        default_factory=list
    )  # what the before-capture marked as already mutating
    fingerprint_truncated: dict[str, int] | None = (
        None  # {"fingerprinted": n, "total": m} when a capture hit the node cap
    )
    value_before: str | None = None
    value_after: str | None = None
    value_changed: bool = False
    modals_opened: list[str] = field(default_factory=list)
    modals_closed: list[str] = field(default_factory=list)
    screenshot_hamming: int | None = None
    screenshot_changed_fraction: float | None = None
    screenshot_changed: bool = False
    screenshot_masked_regions: int = 0  # background roots' boxes excluded from the pixel diff
    dialogs: list[dict[str, Any]] = field(default_factory=list)
    navigations: list[str] = field(default_factory=list)
    cross_document_navigations: list[str] = field(default_factory=list)
    new_tabs: list[str] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    http_errors: list[dict[str, Any]] = field(
        default_factory=list
    )  # responses >= 400 started after dispatch (<= 5)
    # Controls the browser's constraint validation rejected since dispatch (`invalid` events):
    # {path, tag, id, name, type, message, flags}. Non-empty means the page refused a submit.
    validation_blocked: list[dict[str, Any]] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    page_crashed: bool = False
    captures_ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        if self.screenshot_changed_fraction is not None:
            d["screenshot_changed_fraction"] = round(self.screenshot_changed_fraction, 4)
        return d

    @property
    def any_change(self) -> bool:
        return any(
            [
                self.url_changed,
                self.title_changed,
                self.scroll_dx,
                self.scroll_dy,
                self.container_scroll and self.container_scroll.get("delta"),
                self.frame_scrolls,
                self.focus_changed,
                self.dom_changed,
                self.text_changed,
                self.value_changed,
                self.modals_opened,
                self.modals_closed,
                self.screenshot_changed,
                self.dialogs,
                self.navigations,
                self.new_tabs,
                self.downloads,
            ]
        )


def _strip_fragment(u: str) -> str:
    p = urlsplit(u)
    return p._replace(fragment="").geturl()


def compute_delta(before: Capture, after: Capture, events: dict[str, Any]) -> Delta:
    d = Delta()
    d.captures_ok = before.ok and after.ok
    d.url_before, d.url_after = before.url, after.url
    if before.url != after.url:
        if _strip_fragment(before.url) == _strip_fragment(after.url):
            d.url_only_fragment = True
        else:
            d.url_changed = True
    d.title_changed = before.title != after.title

    if before.scroll and after.scroll:
        d.scroll_dx = int(after.scroll.get("x", 0) - before.scroll.get("x", 0))
        d.scroll_dy = int(after.scroll.get("y", 0) - before.scroll.get("y", 0))

    # Target's own scroll / scrollable ancestor (for scroll-in-container actions)
    tb, ta = before.target or {}, after.target or {}
    if tb and ta and "error" not in tb and "error" not in ta:
        if tb.get("selfScrollable") or ta.get("selfScrollable"):
            delta = int(ta.get("scrollTop", 0) - tb.get("scrollTop", 0))
            d.container_scroll = {
                "path": ta.get("path"),
                "before": tb.get("scrollTop"),
                "after": ta.get("scrollTop"),
                "delta": delta,
                "scrollHeight": ta.get("scrollHeight"),
                "clientHeight": ta.get("clientHeight"),
            }
        elif tb.get("scrollableAncestor") and ta.get("scrollableAncestor"):
            sb, sa = tb["scrollableAncestor"], ta["scrollableAncestor"]
            d.container_scroll = {
                "path": sa.get("path"),
                "before": sb.get("scrollTop"),
                "after": sa.get("scrollTop"),
                "delta": int(sa.get("scrollTop", 0) - sb.get("scrollTop", 0)),
                "scrollHeight": sa.get("scrollHeight"),
                "clientHeight": sa.get("clientHeight"),
            }
        d.value_before = tb.get("value")
        d.value_after = ta.get("value")
        d.value_changed = d.value_before != d.value_after

    # Same-origin iframes whose own window scrolled (matched by path).
    fb = {f["path"]: f for f in before.frames if "y" in f}
    for f in after.frames:
        b = fb.get(f["path"])
        if b is None or "y" not in f:
            continue
        dy, dx = int(f["y"] - b["y"]), int(f["x"] - b["x"])
        if dy or dx:
            d.frame_scrolls.append(
                {
                    "path": f["path"],
                    "before": b["y"],
                    "after": f["y"],
                    "delta": dy,
                    "dx": dx,
                    "scrollHeight": f.get("scrollHeight"),
                    "clientHeight": f.get("clientHeight"),
                }
            )

    fb_path = (before.focused or {}).get("path")
    fa_path = (after.focused or {}).get("path")
    d.focus_before, d.focus_after = fb_path, fa_path
    d.focus_changed = fb_path != fa_path

    d.dom_changed = before.dom_hash != after.dom_hash
    d.element_count_delta = after.element_total - before.element_total
    d.text_changed = before.text_hash != after.text_hash
    d.background_roots = list(before.background)
    if d.dom_changed:
        bn, an = before.nodes, after.nodes
        bgp = before.background_paths | after.background_paths
        added = [p for p in an if p not in bn]
        removed = [p for p in bn if p not in an]
        modified = [p for p in bn if p in an and bn[p] != an[p]]
        background = [p for p in added + removed + modified if p in bgp]
        if background:
            added = [p for p in added if p not in bgp]
            removed = [p for p in removed if p not in bgp]
            modified = [p for p in modified if p not in bgp]
            d.nodes_background = background[:MAX_LISTED_NODES]
            d.nodes_background_total = len(background)
        d.nodes_changed_total = len(added) + len(removed) + len(modified)
        d.nodes_added = added[:MAX_LISTED_NODES]
        d.nodes_removed = removed[:MAX_LISTED_NODES]
        d.nodes_modified = [
            {"path": p, "before": bn[p][:160], "after": an[p][:160]} for p in modified[:MAX_LISTED_NODES]
        ]
        # The fingerprint differs only because of background nodes: not a change the action made.
        if background and d.nodes_changed_total == 0:
            d.dom_changed = False
    if before.truncated or after.truncated:
        cap = after if after.truncated else before
        d.fingerprint_truncated = {"fingerprinted": cap.element_count, "total": cap.element_total}

    mb, ma = set(before.modals), set(after.modals)
    d.modals_opened = sorted(ma - mb)
    d.modals_closed = sorted(mb - ma)

    if before.screenshot and after.screenshot:
        d.screenshot_hamming = hamming(before.screenshot.dhash, after.screenshot.dhash)
        # Background roots' boxes (before and after: a rAF-moved element occupies both) are set
        # aside, the way their nodes are set aside from the DOM delta.
        rects = before.background_rects + after.background_rects
        vp = (before.screenshot.width, before.screenshot.height)
        d.screenshot_changed_fraction = changed_fraction(
            before.screenshot.small, after.screenshot.small, mask_rects=rects or None, viewport=vp
        )
        d.screenshot_masked_regions = len(rects)
        d.screenshot_changed = d.screenshot_changed_fraction >= SCREENSHOT_CHANGED_THRESHOLD

    d.dialogs = events.get("dialogs", [])
    d.navigations = events.get("navigations", [])
    d.cross_document_navigations = events.get("cross_document_navigations", [])
    d.new_tabs = events.get("new_tab_urls", [])
    d.downloads = events.get("downloads", [])
    d.http_errors = events.get("http_errors", [])
    # Recorded by the observer between the dispatch mark and the after-capture (the before-capture's
    # list, if any, belongs to the previous action and is ignored).
    d.validation_blocked = list(after.validation)
    d.console_errors = events.get("console_errors", [])
    d.page_crashed = bool(events.get("crashed", False))
    return d
