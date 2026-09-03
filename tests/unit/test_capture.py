"""The two image measures (dHash, changed-pixel fraction), PNG decoding and the capture summary
contract - pure PIL, no browser."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from action_receipt.capture import _DIFF_WIDTH, Capture, changed_fraction, dhash, hamming, screenshot_from_png
from action_receipt.schema import CaptureSummary

from ._synth import capture, image


def _gradient(w=64, h=64):
    img = Image.new("L", (w, h))
    img.putdata(
        [int(255 * (w - 1 - x) / w) for _ in range(h) for x in range(w)]
    )  # bright -> dark, left to right
    return img


def test_dhash_is_64_bits_and_flat_images_hash_to_zero():
    assert dhash(image()) == 0
    g = dhash(_gradient())
    assert g == (1 << 64) - 1  # every left neighbour is brighter than its right one
    assert dhash(_gradient()) == g  # deterministic


def test_dhash_of_a_mirrored_gradient_is_the_complement():
    g = _gradient()
    m = g.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert hamming(dhash(g), dhash(m)) == 64


def test_hamming_counts_differing_bits():
    assert hamming(0, 0) == 0
    assert hamming(0b1010, 0b0101) == 4
    assert hamming((1 << 64) - 1, 0) == 64


def test_changed_fraction_identical_half_and_threshold():
    a = image(160, 100)
    assert changed_fraction(a, a) == 0.0
    b = a.copy()
    ImageDraw.Draw(b).rectangle([0, 0, 79, 99], fill=0)
    assert abs(changed_fraction(a, b) - 0.5) < 1e-6
    faint = a.point(lambda v: v - 10)  # below the 24-level pixel delta: not a change
    assert changed_fraction(a, faint) == 0.0


def test_changed_fraction_resizes_a_mismatched_image():
    a, b = image(160, 100), image(80, 50, colour=0)
    assert changed_fraction(a, b) == 1.0


def test_changed_fraction_masks_background_rects_in_viewport_coordinates():
    a = image(160, 100)
    b = a.copy()
    ImageDraw.Draw(b).rectangle([0, 0, 79, 99], fill=0)
    # viewport 1600x1000 -> the mask [0,0,800,1000] covers exactly the changed left half
    assert changed_fraction(a, b, mask_rects=[[0, 0, 800, 1000]], viewport=(1600, 1000)) < 0.02
    # a mask elsewhere leaves the change visible; a zero viewport disables masking
    assert changed_fraction(a, b, mask_rects=[[800, 0, 1600, 1000]], viewport=(1600, 1000)) > 0.45
    assert abs(changed_fraction(a, b, mask_rects=[[0, 0, 800, 1000]], viewport=(0, 0)) - 0.5) < 1e-6


def test_screenshot_from_png_downscales_to_the_diff_width():
    img = Image.new("RGB", (640, 400), (200, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    s = screenshot_from_png(buf.getvalue())
    assert (s.width, s.height) == (640, 400)
    assert s.small.size == (_DIFF_WIDTH, 100) and s.small.mode == "L"
    assert s.dhash == 0  # flat colour


def test_capture_summary_matches_the_schema_and_omits_nodes_and_images():
    c = capture(nodes={"a": "1", "b": "2"})
    s = c.summary()
    assert set(s) == set(CaptureSummary.model_fields), set(s) ^ set(CaptureSummary.model_fields)
    assert "nodes" not in s and "screenshot" not in s and "background_paths" not in s
    assert s["screenshot_dhash"] is None and s["element_count"] == 2
    CaptureSummary.model_validate(s)


def test_capture_summary_formats_the_dhash_as_16_hex_digits():
    c = capture()
    c.screenshot = screenshot_from_png(_png())
    s = c.summary()
    assert len(s["screenshot_dhash"]) == 16 and int(s["screenshot_dhash"], 16) == c.screenshot.dhash


def _png() -> bytes:
    buf = io.BytesIO()
    _gradient().save(buf, format="PNG")
    return buf.getvalue()


def test_failed_capture_defaults():
    c = Capture(ok=False, ts=0.0, elapsed_ms=1.0, error="capture failed: Error: closed")
    s = c.summary()
    assert not s["ok"] and s["error"].startswith("capture failed") and s["dom_hash"] == "" and s["url"] == ""
