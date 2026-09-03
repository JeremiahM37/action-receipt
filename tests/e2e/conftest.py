"""e2e scaffolding: the real MCP server over stdio (as a client would launch it), and a
Chromium launched by the test with a remote-debugging port for the CDP-attach and wrap-mode
flows. Nothing here touches a browser the user owns."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

from action_receipt.schema import validate_receipt
from tests.conftest import (  # noqa: F401 - re-exported fixture + helpers
    cdp_alive,
    cdp_open_tab,
    cdp_targets,
    own_chromium,
)

# ``get_default_environment()`` is the whitelist a real MCP client hands its server (HOME, PATH,
# USER, ...). It drops PLAYWRIGHT_BROWSERS_PATH, so a server launched under it looks for Chromium
# in ~/.cache/ms-playwright even when the browsers were installed somewhere else (CI does this to
# cache them per Playwright version) - and every action tool then fails with a structured
# ``Error: Executable doesn't exist``. Forward it explicitly, the way a client config would.
FORWARDED_ENV = ("PLAYWRIGHT_BROWSERS_PATH",)


def server_params(*args: str, **env_over: str) -> StdioServerParameters:
    env = dict(get_default_environment())
    for key in FORWARDED_ENV:
        if os.environ.get(key):
            env[key] = os.environ[key]
    env["AR_SETTLE_TIMEOUT_MS"] = "8000"
    env["AR_QUIET_MS"] = "100"
    env.update(env_over)
    return StdioServerParameters(command=sys.executable, args=["-m", "action_receipt.server", *args], env=env)


@asynccontextmanager
async def mcp_client(*args: str, **env_over: str):
    """An initialised ClientSession talking to a freshly launched action-receipt server."""
    async with stdio_client(server_params(*args, **env_over)) as (read, write):
        async with ClientSession(read, write) as cs:
            init = await cs.initialize()
            assert init.server_info.name == "action-receipt"
            yield cs


async def call(cs: ClientSession, tool: str, **args):
    """Call an action tool and return its receipt dict after validating it; the raw structured
    content is available as ``receipt["_result"]`` for tools that also return a result."""
    res = await cs.call_tool(tool, args)
    assert not res.is_error, res
    out = res.structured_content
    # A tool failure is a structured ``{"error": {...}, "receipt": None}``, never a protocol error;
    # surface it here so a failing step reports the server's reason instead of a bare TypeError
    # on ``receipt[...]`` two lines later.
    assert not out.get("error"), f"{tool}({args}) returned a structured error: {out['error']}"
    if "receipt" in out and out["receipt"] is not None:
        validate_receipt(out["receipt"])
    return out


def aria_of(snapshot: dict) -> str:
    return snapshot.get("aria") or ""
