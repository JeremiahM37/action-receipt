"""CDP-attach mode through the real server: a Chromium the test launched with a remote
debugging port has a tab of its own; the server is started with ``--cdp`` and runs a flow;
the external browser's tabs are untouched and it is still running afterwards."""

from __future__ import annotations

import asyncio

from .conftest import call, cdp_alive, cdp_open_tab, cdp_targets, mcp_client


async def test_cdp_new_context_leaves_the_external_tabs_alone(own_chromium, base_url):
    # A tab that belongs to the "user": opened via the browser's own HTTP endpoint, no client attached.
    theirs = cdp_open_tab(own_chromium, f"{base_url}/short.html")
    before = cdp_targets(own_chromium)
    assert theirs in before and before[theirs].endswith("/short.html")

    async with mcp_client("--cdp", own_chromium, "--cdp-new-context") as cs:
        rc = (await call(cs, "open", url=f"{base_url}/buttons.html"))["receipt"]
        assert rc["verdict"] == "navigated"
        rc = (await call(cs, "click", selector="#counter-btn"))["receipt"]
        assert rc["verdict"] == "changed"
        assert any(m["path"].endswith("span#count") for m in rc["delta"]["nodes_modified"])
        rc = (await call(cs, "click", selector="#disabled-btn"))["receipt"]
        assert rc["verdict"] == "blocked"

        # the server's tab list is only what it opened itself (an isolated context)
        tabs = (await call(cs, "tabs"))["tabs"]
        assert [t["url"].rsplit("/", 1)[-1] for t in tabs] == ["buttons.html"]
        # and the browser still reports the user's tab, unchanged, next to ours
        during = cdp_targets(own_chromium)
        assert during[theirs] == before[theirs]
        assert any(u.endswith("/buttons.html") for u in during.values())

    # after the server exits its context is gone; the user's tab and the browser remain
    for _ in range(50):
        after = cdp_targets(own_chromium)
        if not any(u.endswith("/buttons.html") for u in after.values()):
            break
        await asyncio.sleep(0.1)
    assert cdp_alive(own_chromium)
    assert after[theirs] == before[theirs]
    assert not any(u.endswith("/buttons.html") for u in after.values()), after


async def test_cdp_default_context_shares_the_users_tabs_for_wrap_mode(own_chromium, base_url):
    """Without --cdp-new-context the server shares the default context: it can see (and observe)
    a tab it did not open, which is what wrap mode around another tool needs."""
    theirs = cdp_open_tab(own_chromium, f"{base_url}/buttons.html")
    async with mcp_client("--cdp", own_chromium) as cs:
        tabs = (await call(cs, "tabs"))["tabs"]
        assert any(t["url"].endswith("/buttons.html") for t in tabs)
        rc = await call(
            cs, "receipt_begin", label="external-noop", page_url="buttons.html", selector="#counter-btn"
        )
        assert rc["receipt_id"] and rc["observing"].endswith("/buttons.html")
        rc = (await call(cs, "receipt_end"))["receipt"]
        assert rc["verdict"] == "no_op" and rc["dispatch"]["mode"] == "wrap"
    after = cdp_targets(own_chromium)
    assert after[theirs].endswith("/buttons.html") and cdp_alive(own_chromium)
