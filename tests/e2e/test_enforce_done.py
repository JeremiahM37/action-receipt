"""The done gate through the real MCP server: with ``--enforce-done`` a ``done`` is refused while
the last receipt is no_op / blocked / unknown (or nothing has been done), accepted after a
changed / navigated receipt, and accepted as ``overridden`` once the refusal cap is reached.
Without the flag ``done`` always accepts, so the tool is safe in every manifest."""

from __future__ import annotations

from .conftest import call, mcp_client


async def test_enforced_done_refuses_until_an_action_took_effect(base_url):
    async with mcp_client("--enforce-done") as cs:
        pol = await call(cs, "receipt_policy")
        assert pol["enforce"] is True and pol["max_refusals"] == 2
        assert pol["refusals"] == 0 and pol["actions"] == 0 and pol["last_verdict"] is None

        # 1. nothing has been done: refused, and no browser is needed to say so
        out = await call(cs, "done", summary="finished", claimed_effects=["nothing, really"])
        assert out["accepted"] is False and out["overridden"] is False
        assert out["reason"] == "no action has been performed in this session"
        assert "no action has been performed" in out["hint"] and "(1/2)" in out["instruction"]
        assert out["refusals"] == 1 and out["last_receipt"] is None and out["actions"] == 0
        assert out["summary"] == "finished" and out["claimed_effects"] == ["nothing, really"]
        assert (await call(cs, "receipt_policy"))["refusals"] == 1

        # 2. a navigated receipt: accepted; the result reports the refusals this claim went
        #    through, and the live counter starts over for the next claim
        rc = (await call(cs, "open", url=f"{base_url}/buttons.html"))["receipt"]
        assert rc["verdict"] == "navigated"
        out = await call(cs, "done", summary="opened the page")
        assert out["accepted"] is True and out["overridden"] is False and out["reason"] is None
        assert out["refusals"] == 1 and out["last_receipt"]["verdict"] == "navigated"
        assert out["last_receipt"]["id"] == rc["id"] and out["actions"] == 1
        assert (await call(cs, "receipt_policy"))["refusals"] == 0

        # 3. a blocked receipt: refused twice with the receipt's own hint, then overridden
        rc = (await call(cs, "click", selector="#disabled-btn"))["receipt"]
        assert rc["verdict"] == "blocked"
        out = await call(cs, "done", summary="clicked it")
        assert out["accepted"] is False and out["reason"] == "the last action's receipt verdict was blocked"
        assert out["hint"] == rc["hint"] and out["last_receipt"]["id"] == rc["id"]
        assert out["last_receipt"]["evidence"] == rc["evidence"] and out["refusals"] == 1
        out = await call(cs, "done", summary="clicked it")
        assert out["accepted"] is False and out["refusals"] == 2 and "(2/2)" in out["instruction"]
        out = await call(cs, "done", summary="clicked it")
        assert out["accepted"] is True and out["overridden"] is True and out["refusals"] == 2
        assert out["reason"] == "the last action's receipt verdict was blocked" and out["instruction"] is None
        pol = await call(cs, "receipt_policy")
        assert pol["refusals"] == 0 and pol["last_verdict"] == "blocked" and pol["actions"] == 2

        # 4. no_op -> refused; a changed receipt clears it without needing the cap
        rc = (await call(cs, "scroll", direction="down"))["receipt"]
        assert rc["verdict"] == "no_op"
        out = await call(cs, "done", summary="scrolled")
        assert out["accepted"] is False and out["reason"] == "the last action's receipt verdict was no_op"
        assert "not scrollable" in out["hint"]
        rc = (await call(cs, "click", selector="#counter-btn"))["receipt"]
        assert rc["verdict"] == "changed"
        out = await call(cs, "done", summary="incremented the counter")
        assert out["accepted"] is True and out["overridden"] is False and out["refusals"] == 1

        # 5. wrap-mode receipts count too: an idle begin/end is a no_op and gates the next done
        await call(cs, "receipt_begin", label="external-idle", selector="#counter-btn")
        rc = (await call(cs, "receipt_end"))["receipt"]
        assert rc["verdict"] == "no_op" and rc["dispatch"]["mode"] == "wrap"
        out = await call(cs, "done", summary="the other tool did it")
        assert out["accepted"] is False and out["last_receipt"]["action"] == "external-idle"


async def test_done_always_accepts_when_enforcement_is_off(base_url):
    async with mcp_client() as cs:
        pol = await call(cs, "receipt_policy")
        assert pol["enforce"] is False and pol["refusals"] == 0
        out = await call(cs, "done", summary="nothing happened yet")
        assert out == {
            **out,
            "accepted": True,
            "overridden": False,
            "reason": None,
            "hint": None,
            "instruction": None,
            "enforced": False,
            "refusals": 0,
            "last_receipt": None,
        }
        await call(cs, "open", url=f"{base_url}/buttons.html")
        rc = (await call(cs, "click", selector="#disabled-btn"))["receipt"]
        assert rc["verdict"] == "blocked"
        out = await call(cs, "done", summary="clicked the disabled button")
        assert out["accepted"] is True and out["overridden"] is False and out["enforced"] is False
        assert out["last_receipt"]["verdict"] == "blocked"  # the information is there, unenforced


async def test_env_flag_and_refusal_cap(base_url):
    async with mcp_client("--max-refusals", "1", AR_ENFORCE_DONE="1") as cs:
        pol = await call(cs, "receipt_policy")
        assert pol["enforce"] is True and pol["max_refusals"] == 1
        await call(cs, "open", url=f"{base_url}/buttons.html")
        assert (await call(cs, "click", selector="#noop-btn"))["receipt"]["verdict"] == "no_op"
        out = await call(cs, "done", summary="pressed the button")
        assert out["accepted"] is False and out["refusals"] == 1 and "(1/1)" in out["instruction"]
        out = await call(cs, "done", summary="pressed the button")
        assert out["accepted"] is True and out["overridden"] is True and out["refusals"] == 1
