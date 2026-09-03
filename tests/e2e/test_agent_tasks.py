"""A scripted "agent" drives three bench tasks purely through MCP tool calls, reads the receipt
after every step the way an agent would, and recovers from what the receipt says. Each task is
one of the traps the bench was built around: a silent-validation ``no_op``, a covered-target
``blocked``, and a new-tab ``navigated``. The final DOM state is checked through ``snapshot``.

The apps are ``bench/fixtures/apps/*`` served by the shared fixture server under ``/bench/``."""

from __future__ import annotations

from .conftest import aria_of, call, mcp_client


async def test_silent_validation_no_op_then_recovery(base_url):
    """bench task profile_save_phone: Save returns early while the (subtly required) phone field is
    empty - no message, no error. The receipt is the only tell."""
    async with mcp_client() as cs:
        rc = (await call(cs, "open", url=f"{base_url}/bench/apps/profile.html?strict=1"))["receipt"]
        assert rc["verdict"] == "navigated"

        rc = (await call(cs, "type", selector="#display", text="Ada", clear=True))["receipt"]
        assert rc["verdict"] == "changed" and rc["delta"]["value_after"] == "Ada"

        rc = (await call(cs, "click", selector="#save"))["receipt"]
        assert rc["verdict"] == "no_op", rc["evidence"]
        assert rc["evidence"][0] == "no_observable_change"
        assert "click was delivered to button#save" in rc["hint"]
        # nothing was saved: the page still shows the defaults
        snap = await call(cs, "snapshot", aria=True)
        assert "display=Guest" in aria_of(snap)

        # the agent recovers: the label says "(required)" - fill the phone and save again
        rc = (await call(cs, "type", selector="#phone", text="555"))["receipt"]
        assert rc["verdict"] == "changed"
        rc = (await call(cs, "click", selector="#save"))["receipt"]
        assert rc["verdict"] == "changed", rc["evidence"]
        # the save is an 800 ms fetch: settlement waited for it and the receipt carries the result
        assert rc["settlement"]["settled_by"] in ("network", "dom")
        assert rc["settlement"]["elapsed_ms"] >= 800
        assert any(
            m["path"].endswith("p#saved-view") and "display=Ada" in m["after"]
            for m in rc["delta"]["nodes_modified"]
        ), rc["delta"]["nodes_modified"]
        snap = await call(cs, "snapshot", aria=True)
        assert "display=Ada, phone=555" in aria_of(snap) and "Saved" in aria_of(snap)


async def test_covered_target_blocked_then_recovery(base_url):
    """bench task cart_consent_checkout: a 330 px consent banner covers the products and the
    Checkout button; every click on them lands on the banner."""
    async with mcp_client() as cs:
        await call(cs, "open", url=f"{base_url}/bench/apps/cart.html?banner=1")

        rc = (await call(cs, "click", selector="#add-gadget"))["receipt"]
        assert rc["verdict"] == "blocked", rc["evidence"]
        assert rc["evidence"][0].startswith("target_covered_by: div#consent")
        assert "another element receives the pointer" in rc["hint"]
        assert (
            rc["dispatch"]["ok"] is False and rc["dispatch"]["elapsed_ms"] < 1500
        )  # short timeout: pre-flight saw the cover

        rc = (await call(cs, "click", selector="#consent-accept"))["receipt"]
        assert rc["verdict"] == "changed" and any(
            p.endswith("div#consent") or "div#consent" in p
            for p in [m["path"] for m in rc["delta"]["nodes_modified"]] + rc["delta"]["nodes_removed"]
        )

        rc = (await call(cs, "click", selector="#add-gadget"))["receipt"]
        assert rc["verdict"] == "changed", rc["evidence"]
        rc = (await call(cs, "click", selector="#checkout"))["receipt"]
        assert rc["verdict"] == "changed", rc["evidence"]
        assert any(p.endswith("p#order-text") for p in rc["delta"]["nodes_added"]), rc["delta"]["nodes_added"]
        snap = await call(cs, "snapshot", aria=True)
        assert "Order placed" in aria_of(snap) and "Gadget" in aria_of(snap)


async def test_new_tab_navigated_then_confirm_in_the_new_tab(base_url):
    """bench task notes_publish_newtab: Publish opens a new tab; the confirmation lives there."""
    async with mcp_client() as cs:
        await call(cs, "open", url=f"{base_url}/bench/apps/notes.html")
        rc = (await call(cs, "type", selector="#title", text="Q3 plan", clear=True))["receipt"]
        assert rc["verdict"] == "changed"

        rc = (await call(cs, "click", selector="#publish"))["receipt"]
        assert rc["verdict"] == "navigated", rc["evidence"]
        assert rc["delta"]["new_tabs"] and rc["delta"]["new_tabs"][0].endswith("publish.html?title=Q3%20plan")
        assert not rc["delta"]["url_changed"]  # the original tab stayed put
        assert "a new tab opened at" in rc["hint"] and "switch to it" in rc["hint"]

        tabs = (await call(cs, "tabs"))["tabs"]
        assert (
            len(tabs) == 2 and tabs[0]["current"] and tabs[1]["url"].endswith("publish.html?title=Q3%20plan")
        )
        tabs = (await call(cs, "tabs", select=1))["tabs"]
        assert tabs[1]["current"] and not tabs[0]["current"]

        rc = (await call(cs, "click", selector="#confirm-publish"))["receipt"]
        assert rc["verdict"] == "changed", rc["evidence"]
        assert any(
            m["path"].endswith("span#status") and "Published" in m["after"]
            for m in rc["delta"]["nodes_modified"]
        )
        snap = await call(cs, "snapshot", aria=True)
        assert snap["url"].endswith("publish.html?title=Q3%20plan") and "Published" in aria_of(snap)
        assert len(snap["pages"]) == 2

        # the confirm button is now disabled: a second click is a cheap blocked, not a retry
        rc = (await call(cs, "click", selector="#confirm-publish"))["receipt"]
        assert rc["verdict"] == "blocked" and rc["evidence"][0].startswith("target_disabled")

        # receipt_last sees the whole episode in order
        last = (await call(cs, "receipt_last", n=3))["receipts"]
        assert [r["verdict"] for r in last] == ["navigated", "changed", "blocked"]
