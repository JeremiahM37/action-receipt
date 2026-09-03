"""compute_delta: the deterministic diff of two captures - bounded node lists, background
exclusion, truncation flag, fragment-only URLs, screenshot measures - no browser."""

from __future__ import annotations

from PIL import ImageDraw

from action_receipt.delta import (
    MAX_LISTED_NODES,
    SCREENSHOT_CHANGED_THRESHOLD,
    Delta,
    _strip_fragment,
    compute_delta,
)

from ._synth import URL, capture, image, screenshot

BASE = {"html:nth-of-type(1)": "html", "body:nth-of-type(1)": "body", "span#n": "span|t=0"}


def test_identical_captures_produce_an_empty_delta():
    a, b = capture(nodes=BASE), capture(nodes=BASE)
    d = compute_delta(a, b, {})
    assert not d.any_change and d.captures_ok
    assert d.url_before == d.url_after == URL
    assert d.nodes_changed_total == 0 and d.element_count_delta == 0
    assert d.screenshot_hamming is None and d.screenshot_changed_fraction is None


def test_captures_ok_requires_both():
    d = compute_delta(capture(ok=False), capture(), {})
    assert not d.captures_ok


def test_url_change_vs_fragment_only():
    assert _strip_fragment("http://h/p?q=1#frag") == "http://h/p?q=1"
    d = compute_delta(capture(url=URL), capture(url=URL + "#sec"), {})
    assert d.url_only_fragment and not d.url_changed
    d = compute_delta(capture(url=URL), capture(url=URL + "?next=1"), {})
    assert d.url_changed and not d.url_only_fragment
    assert d.any_change


def test_title_and_scroll_and_focus():
    a = capture(title="A", scroll={"x": 0, "y": 0}, focused={"path": "input#a"})
    b = capture(title="B", scroll={"x": 10, "y": 250}, focused={"path": "input#b"})
    d = compute_delta(a, b, {})
    assert d.title_changed and (d.scroll_dx, d.scroll_dy) == (10, 250)
    assert d.focus_changed and (d.focus_before, d.focus_after) == ("input#a", "input#b")
    d = compute_delta(capture(focused=None), capture(focused=None), {})
    assert not d.focus_changed and d.focus_before is None


def test_added_removed_modified_nodes_are_listed():
    after = {**BASE, "span#n": "span|t=1", "li#new": "li|x"}
    del after["body:nth-of-type(1)"]
    d = compute_delta(capture(nodes=BASE), capture(nodes=after), {})
    assert d.dom_changed and d.text_changed
    assert d.nodes_added == ["li#new"] and d.nodes_removed == ["body:nth-of-type(1)"]
    assert d.nodes_modified == [{"path": "span#n", "before": "span|t=0", "after": "span|t=1"}]
    assert d.nodes_changed_total == 3 and d.element_count_delta == 0


def test_node_lists_are_bounded_but_the_total_is_not():
    before = dict(BASE)
    after = {**BASE, **{f"li#i{i}": "li" for i in range(30)}}
    d = compute_delta(capture(nodes=before), capture(nodes=after), {})
    assert len(d.nodes_added) == MAX_LISTED_NODES == 12
    assert d.nodes_changed_total == 30 and d.element_count_delta == 30


def test_modified_signatures_are_clipped_to_160_chars():
    a = {**BASE, "p#x": "p|" + "a" * 500}
    b = {**BASE, "p#x": "p|" + "b" * 500}
    d = compute_delta(capture(nodes=a), capture(nodes=b), {})
    m = d.nodes_modified[0]
    assert len(m["before"]) == 160 and len(m["after"]) == 160


def test_background_nodes_are_set_aside_and_alone_do_not_make_a_dom_change():
    a = capture(
        nodes={**BASE, "span#clock": "span|12:00"}, background=["span#clock"], background_paths={"span#clock"}
    )
    b = capture(nodes={**BASE, "span#clock": "span|12:01"})
    d = compute_delta(a, b, {})
    assert not d.dom_changed
    assert d.nodes_background == ["span#clock"] and d.nodes_background_total == 1
    assert d.background_roots == ["span#clock"]
    assert d.nodes_changed_total == 0 and d.nodes_modified == []
    # a real change next to the background one still counts, and lists only the real one
    b2 = capture(nodes={**BASE, "span#clock": "span|12:02", "span#n": "span|t=1"})
    d = compute_delta(a, b2, {})
    assert d.dom_changed and d.nodes_changed_total == 1
    assert [m["path"] for m in d.nodes_modified] == ["span#n"] and d.nodes_background == ["span#clock"]


def test_truncation_flag_names_the_truncated_capture():
    d = compute_delta(capture(nodes=BASE), capture(nodes=BASE, truncated=True, element_total=25000), {})
    assert d.fingerprint_truncated == {"fingerprinted": 3, "total": 25000}
    d = compute_delta(capture(nodes=BASE, truncated=True, element_total=9), capture(nodes=BASE), {})
    assert d.fingerprint_truncated == {"fingerprinted": 3, "total": 9}
    assert compute_delta(capture(), capture(), {}).fingerprint_truncated is None


def test_target_value_and_container_scroll():
    tb = {
        "path": "div#p",
        "selfScrollable": True,
        "scrollTop": 0,
        "scrollHeight": 1000,
        "clientHeight": 400,
        "value": None,
    }
    ta = {**tb, "scrollTop": 300}
    d = compute_delta(capture(target=tb), capture(target=ta), {})
    assert d.container_scroll == {
        "path": "div#p",
        "before": 0,
        "after": 300,
        "delta": 300,
        "scrollHeight": 1000,
        "clientHeight": 400,
    }
    assert not d.value_changed
    anc_b = {
        "path": "span#t",
        "value": "a",
        "scrollableAncestor": {"path": "div#p", "scrollTop": 10, "scrollHeight": 900, "clientHeight": 300},
    }
    anc_a = {
        "path": "span#t",
        "value": "ab",
        "scrollableAncestor": {"path": "div#p", "scrollTop": 50, "scrollHeight": 900, "clientHeight": 300},
    }
    d = compute_delta(capture(target=anc_b), capture(target=anc_a), {})
    assert d.container_scroll["delta"] == 40 and d.container_scroll["path"] == "div#p"
    assert d.value_changed and (d.value_before, d.value_after) == ("a", "ab")


def test_target_probe_errors_disable_target_fields():
    d = compute_delta(capture(target={"error": "detached"}), capture(target={"path": "x", "value": "v"}), {})
    assert d.container_scroll is None and d.value_before is None and not d.value_changed


def test_frame_scrolls_are_matched_by_path():
    fb = [
        {"path": "iframe#f", "x": 0, "y": 0, "scrollHeight": 900, "clientHeight": 300},
        {"path": "iframe#x"},
    ]
    fa = [
        {"path": "iframe#f", "x": 0, "y": 200, "scrollHeight": 900, "clientHeight": 300},
        {"path": "iframe#new", "x": 0, "y": 5},
    ]
    d = compute_delta(capture(frames=fb), capture(frames=fa), {})
    assert d.frame_scrolls == [
        {
            "path": "iframe#f",
            "before": 0,
            "after": 200,
            "delta": 200,
            "dx": 0,
            "scrollHeight": 900,
            "clientHeight": 300,
        }
    ]


def test_modals_opened_and_closed_are_sorted_set_differences():
    d = compute_delta(capture(modals=["div#b", "div#a"]), capture(modals=["div#b", "div#z", "div#c"]), {})
    assert d.modals_opened == ["div#c", "div#z"] and d.modals_closed == ["div#a"]


def test_screenshot_measures_identical_and_different():
    same = compute_delta(capture(shot=screenshot(image())), capture(shot=screenshot(image())), {})
    assert (
        same.screenshot_hamming == 0
        and same.screenshot_changed_fraction == 0.0
        and not same.screenshot_changed
    )
    half = image()
    ImageDraw.Draw(half).rectangle([0, 0, 79, 95], fill=0)
    d = compute_delta(capture(shot=screenshot(image())), capture(shot=screenshot(half)), {})
    assert d.screenshot_changed and abs(d.screenshot_changed_fraction - 0.5) < 0.02
    assert d.screenshot_hamming >= 1
    assert d.screenshot_changed_fraction >= SCREENSHOT_CHANGED_THRESHOLD


def test_background_rects_mask_the_pixel_diff():
    moved = image()
    ImageDraw.Draw(moved).rectangle([0, 0, 79, 95], fill=0)  # the left half changed
    # the before-capture marked a background root whose viewport box covers that left half
    a = capture(shot=screenshot(image()), background_rects=[[0, 0, 500, 600]])
    b = capture(shot=screenshot(moved))
    d = compute_delta(a, b, {})
    assert d.screenshot_masked_regions == 1
    assert d.screenshot_changed_fraction < SCREENSHOT_CHANGED_THRESHOLD and not d.screenshot_changed


def test_events_are_copied_into_the_delta():
    ev = {
        "dialogs": [{"type": "alert", "message": "m", "handled": "accept"}],
        "navigations": [URL],
        "cross_document_navigations": [URL],
        "new_tab_urls": ["t"],
        "downloads": [{"filename": "f", "url": "u"}],
        "http_errors": [{"url": "u", "status": 500, "method": "GET"}],
        "console_errors": ["e"],
        "crashed": True,
    }
    d = compute_delta(capture(), capture(), ev)
    assert d.dialogs == ev["dialogs"] and d.navigations == [URL] and d.cross_document_navigations == [URL]
    assert d.new_tabs == ["t"] and d.downloads == ev["downloads"] and d.http_errors == ev["http_errors"]
    assert d.console_errors == ["e"] and d.page_crashed


def test_validation_blocked_comes_from_the_after_capture_only():
    a = capture(
        validation=[
            {
                "path": "input#old",
                "tag": "input",
                "id": "old",
                "name": None,
                "type": None,
                "message": "",
                "flags": [],
            }
        ]
    )
    b = capture(
        validation=[
            {
                "path": "input#phone",
                "tag": "input",
                "id": "phone",
                "name": None,
                "type": "tel",
                "message": "Please fill out this field.",
                "flags": ["valueMissing"],
            }
        ]
    )
    d = compute_delta(a, b, {})
    assert [v["path"] for v in d.validation_blocked] == ["input#phone"]
    assert compute_delta(capture(), capture(), {}).validation_blocked == []


def test_to_dict_rounds_the_fraction_and_keeps_every_field():
    d = Delta(screenshot_changed_fraction=0.123456)
    out = d.to_dict()
    assert out["screenshot_changed_fraction"] == 0.1235
    assert set(out) == set(Delta().__dict__)
    assert Delta().to_dict()["screenshot_changed_fraction"] is None


def test_any_change_reflects_every_effect_field():
    assert not Delta().any_change
    for field, value in [
        ("url_changed", True),
        ("title_changed", True),
        ("scroll_dy", 1),
        ("frame_scrolls", [{}]),
        ("focus_changed", True),
        ("dom_changed", True),
        ("text_changed", True),
        ("value_changed", True),
        ("modals_opened", ["m"]),
        ("modals_closed", ["m"]),
        ("screenshot_changed", True),
        ("dialogs", [{}]),
        ("navigations", ["u"]),
        ("new_tabs", ["u"]),
        ("downloads", [{}]),
        ("container_scroll", {"delta": 3}),
    ]:
        assert Delta(**{field: value}).any_change, field
    assert not Delta(container_scroll={"delta": 0}).any_change
