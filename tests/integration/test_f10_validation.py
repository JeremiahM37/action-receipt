"""Bench finding F10: a submit the browser's constraint validation refuses (`required`, `pattern`,
...) paints a validation bubble that is not in the DOM and used to read as `changed` on a weak
screenshot delta with no hint - the one trap every arm of the agent benchmark failed identically.

The receipt now records the `invalid` events the browser fires on the rejected controls
(delta `validation_blocked`, evidence `form_validation_blocked`) and the verdict is `blocked`
with a hint naming the field and the browser's own message. Fixture: tests/fixtures/validation.html."""

from __future__ import annotations

import pytest


def _ev(r, prefix: str) -> list[str]:
    return [e for e in r.verdict.evidence if e.startswith(prefix)]


def _assert_blocked_by_validation(r, path: str, flag: str, *, action: str):
    assert r.verdict.verdict == "blocked", r.to_dict()
    assert r.dispatch["ok"] is True  # the click/press itself was delivered
    vb = r.delta.validation_blocked
    assert len(vb) == 1 and vb[0]["path"] == path and flag in vb[0]["flags"] and vb[0]["message"], vb
    assert r.verdict.evidence[0].startswith("form_validation_blocked: 1 control(s)"), r.verdict.evidence
    assert path in r.verdict.evidence[0] and flag in r.verdict.evidence[0]
    assert r.verdict.hint.startswith("form validation blocked the submit: " + path), r.verdict.hint
    assert flag in r.verdict.hint and vb[0]["message"] in r.verdict.hint and "retry" in r.verdict.hint
    # nothing else counted: no DOM/value/url change, and the bubble / focus move are stated as the refusal's side effects
    assert (
        not r.delta.dom_changed
        and not r.delta.value_changed
        and not r.delta.url_changed
        and not r.delta.navigations
    )
    assert not _ev(r, "screenshot_changed:") and not _ev(r, "focus_changed")
    for e in _ev(r, "screenshot_changed_weak") + _ev(r, "focus_moved_only"):
        assert "not counted as an effect for " + action in e, e
    assert r.settlement.settled and not r.settlement.timed_out


# ----------------------------------------------------------------------------- 1. required
async def test_f10_required_empty_click_submit_is_blocked_naming_the_field(session, base_url):
    await session.new_page(f"{base_url}/validation.html")
    _, r = await session.click("#save")
    _assert_blocked_by_validation(r, "input#phone", "valueMissing", action="click")
    assert r.verdict.hint.endswith("fill that field and retry"), r.verdict.hint
    assert (await session.current.evaluate("window.__status()")) == ""  # the submit handler never ran
    # the form's own state is in the target probe as context (matches(':invalid'), no events fired by us)
    assert r.dispatch["preflight"]["form"] == {"path": "form#form", "noValidate": False, "invalid": True}
    assert r.after.target["form"]["invalid"] is True


# ----------------------------------------------------------------------------- 2. pattern
async def test_f10_pattern_mismatch_is_blocked_with_the_flag_in_the_receipt(session, base_url):
    await session.new_page(f"{base_url}/validation.html?pattern=1")
    _, r = await session.click("#save")
    _assert_blocked_by_validation(r, "input#code", "patternMismatch", action="click")
    assert r.delta.validation_blocked[0]["flags"] == ["patternMismatch"]
    assert r.delta.validation_blocked[0]["name"] == "code" and r.delta.validation_blocked[0]["tag"] == "input"
    assert r.verdict.hint.endswith("correct that field and retry"), r.verdict.hint


# ----------------------------------------------------------------------------- 3. implicit submission
async def test_f10_enter_in_a_field_of_an_invalid_form_is_blocked(session, base_url):
    """Implicit submission (Enter in a text field) runs the same interactive validation."""
    await session.new_page(f"{base_url}/validation.html")
    _, r = await session.press("Enter", selector="#name")
    _assert_blocked_by_validation(r, "input#phone", "valueMissing", action="press")
    # Enter on the focused submit button itself, without a selector, is caught the same way
    await session.current.focus("#save")
    _, r2 = await session.press("Enter")
    _assert_blocked_by_validation(r2, "input#phone", "valueMissing", action="press")
    # type(..., submit=True): the typed value is a real change, so `changed` - but the receipt still says the submit was refused
    _, r3 = await session.type("#name", "x", submit=True)
    assert r3.verdict.verdict == "changed" and r3.delta.value_changed, r3.to_dict()
    assert r3.delta.validation_blocked and r3.delta.validation_blocked[0]["path"] == "input#phone"
    assert _ev(r3, "form_validation_blocked") and r3.verdict.hint.startswith(
        "form validation blocked the submit: input#phone"
    )
    assert "not the effect of a submit" in r3.verdict.hint, r3.verdict.hint
    assert (await session.current.evaluate("window.__status()")) == ""


# ----------------------------------------------------------------------------- 4. valid form: no regression
async def test_f10_valid_form_submit_is_changed_or_navigated_as_before(session, base_url):
    await session.new_page(f"{base_url}/validation.html?valid=1")
    _, r = await session.click("#save")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert (
        r.delta.validation_blocked == [] and not _ev(r, "form_validation_blocked") and _ev(r, "dom_changed")
    )
    assert "validation" not in (r.verdict.hint or "")
    assert (await session.current.evaluate("window.__status()")) == "Saved"

    await session.new_page(f"{base_url}/validation.html?valid=1&nav=1")
    _, r2 = await session.click("#save")
    assert r2.verdict.verdict == "navigated" and r2.delta.url_changed, r2.to_dict()
    assert r2.delta.validation_blocked == [] and not _ev(r2, "form_validation_blocked")
    assert "/short.html?name=Ada&phone=555" in r2.delta.url_after

    # a click that is not a submit at all on a page whose form is invalid: plain no_op, not blocked
    await session.new_page(f"{base_url}/validation.html")
    _, r3 = await session.click("h1")
    assert r3.verdict.verdict == "no_op" and r3.delta.validation_blocked == [], r3.to_dict()


# ----------------------------------------------------------------------------- 5. novalidate
async def test_f10_novalidate_form_is_not_blocked(session, base_url):
    await session.new_page(f"{base_url}/validation.html?novalidate=1")
    _, r = await session.click("#save")
    assert r.verdict.verdict == "changed", r.to_dict()
    assert r.delta.validation_blocked == [] and not _ev(r, "form_validation_blocked")
    assert (
        r.dispatch["preflight"]["form"]["noValidate"] is True
        and r.dispatch["preflight"]["form"]["invalid"] is True
    )
    assert (await session.current.evaluate("window.__status()")) == "Saved"


# ----------------------------------------------------------------------------- 6. shadow root and same-origin iframe
async def test_f10_validation_inside_a_shadow_root_form_is_blocked(session, base_url):
    """`invalid` is not composed, so it never reaches the document: the observer listens on every
    shadow root it sees (attachShadow hook). The path carries the #shadow-root boundary."""
    await session.new_page(f"{base_url}/validation.html?shadow=1")
    _, r = await session.click("#host #ssave")
    _assert_blocked_by_validation(
        r,
        "div#host>#shadow-root>form#sform>label:nth-of-type(1)>input#sphone",
        "valueMissing",
        action="click",
    )
    assert r.delta.validation_blocked[0]["name"] == "sphone"
    # the light-DOM form on the same page was not touched
    assert (await session.current.evaluate("window.__status()")) == ""


async def test_f10_validation_inside_a_same_origin_iframe_is_blocked(session, base_url):
    """Each frame has its own observer; the after-capture aggregates their rejected controls."""
    await session.new_page(f"{base_url}/validation.html?frame=1")
    await session.current.frame_locator("#f").locator("#fsave").wait_for()
    _, r = await session.click("#fsave", frame="#f")
    _assert_blocked_by_validation(
        r,
        "iframe#f>#document>html>body:nth-of-type(1)>form#form>label:nth-of-type(2)>input#fphone",
        "valueMissing",
        action="click",
    )
    assert r.delta.validation_blocked[0]["name"] == "phone"
    _, r2 = await session.press("Enter", selector="#name", frame="#f")
    assert r2.verdict.verdict == "blocked" and r2.delta.validation_blocked[0]["path"].endswith(
        "input#fphone"
    ), r2.to_dict()


# ----------------------------------------------------------------------------- the bench's own trap
async def test_f10_two_invalid_controls_are_both_named(session, base_url):
    await session.new_page(f"{base_url}/validation.html?pattern=1")
    await session.current.fill("#phone", "")
    _, r = await session.click("#save")
    assert r.verdict.verdict == "blocked", r.to_dict()
    paths = [v["path"] for v in r.delta.validation_blocked]
    assert paths == ["input#phone", "input#code"], paths
    assert (
        "2 control(s)" in r.verdict.evidence[0]
        and "input#phone" in r.verdict.hint
        and "input#code" in r.verdict.hint
    )
    assert r.verdict.hint.endswith("correct those fields and retry"), r.verdict.hint


@pytest.mark.parametrize("how", ["click", "enter"])
async def test_f10_profile_native_trap_from_the_bench_is_blocked(session, base_url, how):
    """The exact bench shape (apps/profile.html?native=1): fill the display name, submit with the
    required phone empty. Before: `changed` on `screenshot_changed: 0.007`, hint null."""
    await session.new_page(f"{base_url}/validation.html")
    await session.current.fill("#name", "Ada")
    if how == "click":
        _, r = await session.click("#save")
    else:
        _, r = await session.press("Enter", selector="#save")
    assert r.verdict.verdict == "blocked" and r.delta.validation_blocked[0]["path"] == "input#phone", (
        r.to_dict()
    )
    assert "Please fill out this field." in r.verdict.hint
    # and once the field is filled the same submit goes through
    _, r2 = await session.type("#phone", "555", clear=True)
    assert r2.verdict.verdict == "changed" and r2.delta.validation_blocked == []
    _, r3 = await session.click("#save")
    assert r3.verdict.verdict == "changed" and r3.delta.validation_blocked == [] and _ev(r3, "dom_changed"), (
        r3.to_dict()
    )
    assert (await session.current.evaluate("window.__status()")) == "Saved"
