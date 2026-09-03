"""Bench 4 - agent-in-the-loop false completion (ties to A14).

    python -m bench.agent_loop [--runs 3] [--max-steps 8] [--model qwen3.6:35b-a3b]
                               [--endpoint http://100.127.85.58:11434/v1] [--seed 20260902]
                               [--think] [--arms receipt_off,receipt_on] [--tasks id,id,...]
                               [--max-calls 900] [--resume] [--summarize-only]

22 small deterministic web tasks on local fixture apps (todo, settings, paginated table, search,
wizard, cart, log panel, terms), each with a programmatic validator over the final DOM state
(``window.__state()``) of every open tab. Nine tasks are traps for naive agents (a covered
button, a silently failing validation, a save that needs a confirm modal, a disabled-until
control, a scroll the window cannot do, a link that opens a new tab, an Enter-only search).

The agent is a text-only LLM (OpenAI-compatible chat endpoint) that sees an element listing
with CSS selectors and acts through the **real action-receipt MCP server** (stdio, attached
over CDP to a Chromium the harness launched). Two arms, identical in every respect except what
the tool result shows the model:

* receipt_off - {"result": ..., "error": <dispatch error or null>}  (what a raw tool returns)
* receipt_on  - the same plus {"receipt": {verdict, evidence, hint, settlement, ...}}

Per episode we log the agent's terminal claim (DONE / FAIL / MAX_STEPS / ERROR), the validator
verdict, steps, model calls, tokens and every receipt. Headline: P(DONE | validator fail) per
arm with Wilson intervals - the per-action analogue of A14's 53.0% on OSWorld.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from typing import Any

from ._common import (
    BENCH_DIR,
    PROJECT_DIR,
    RESULTS,
    FixtureServer,
    env_info,
    env_md,
    fmt_wilson,
    free_port,
    lib_hash,
    log,
    md_table,
    mean,
    stdev,
    wilson,
    write_results,
)

PROMPTS = BENCH_DIR / "prompts"
EPISODES = RESULTS / "agent_loop_episodes.jsonl"
TRACES = RESULTS / "agent_loop_traces"


# ----------------------------------------------------------------------------- tasks
def _st(states: dict[str, Any], name: str) -> dict | None:
    for url, st in states.items():
        if name in url and st:
            return st
    return None


def _has_item(states, text, done=None):
    st = _st(states, "todo.html")
    if not st:
        return False
    for it in st["items"]:
        if it["text"].strip().lower() == text and (done is None or it["done"] == done):
            return True
    return False


def _saved(states, key, val):
    st = _st(states, "settings.html")
    return bool(st) and st["saved"].get(key) == val


def _approved(states, name):
    st = _st(states, "table.html")
    return bool(st) and name in st["approved"]


def _order(states):
    st = _st(states, "cart.html")
    return st["order"] if st and st.get("order") else None


def T(id, app, goal, check, trap=None):
    return {"id": id, "app": app, "goal": goal, "check": check, "trap": trap}


TASKS = [
    T("todo_add", "apps/todo.html", "Add a todo item with the text 'milk'.", lambda S: _has_item(S, "milk")),
    T(
        "todo_add_two",
        "apps/todo.html",
        "Add two todo items: 'milk' and 'eggs'.",
        lambda S: _has_item(S, "milk") and _has_item(S, "eggs"),
    ),
    T(
        "todo_complete",
        "apps/todo.html",
        "Mark the existing todo 'Buy bread' as done.",
        lambda S: _has_item(S, "buy bread", done=True),
    ),
    T(
        "todo_delete",
        "apps/todo.html",
        "Delete the todo 'Call mom'.",
        lambda S: not _has_item(S, "call mom") and _has_item(S, "buy bread"),
    ),
    T(
        "todo_add_banner",
        "apps/todo.html?banner=1",
        "Add a todo item with the text 'milk'.",
        lambda S: _has_item(S, "milk"),
        trap="covered_by_banner",
    ),
    T(
        "settings_name",
        "apps/settings.html",
        "Change the display name to 'Ada' and save the settings.",
        lambda S: _saved(S, "name", "Ada"),
    ),
    T(
        "settings_dark",
        "apps/settings.html",
        "Enable dark mode and save the settings.",
        lambda S: _saved(S, "dark", True),
    ),
    T(
        "settings_strict",
        "apps/settings.html?strict=1",
        "Change the display name to 'Ada' and save the settings.",
        lambda S: _saved(S, "name", "Ada"),
        trap="silent_validation",
    ),
    T(
        "settings_confirm",
        "apps/settings.html?confirm=1",
        "Change the display name to 'Grace' and save the settings.",
        lambda S: _saved(S, "name", "Grace"),
        trap="confirm_modal",
    ),
    T(
        "table_page3",
        "apps/table.html",
        "Approve the applicant named Quinn.",
        lambda S: _approved(S, "Quinn"),
    ),
    T(
        "table_sort_top",
        "apps/table.html",
        "Sort the applicants by score so the highest score is first, then approve the applicant with the highest score.",
        lambda S: (
            bool(_st(S, "table.html"))
            and _st(S, "table.html")["topScoreName"] in _st(S, "table.html")["approved"]
        ),
    ),
    T(
        "table_gate",
        "apps/table.html?gate=1",
        "Approve the applicant named Zed.",
        lambda S: _approved(S, "Zed"),
        trap="disabled_until",
    ),
    T(
        "search_open",
        "apps/search.html",
        "Search for 'kiwi' and open the result titled 'Kiwi facts'.",
        lambda S: (_st(S, "search.html") or {}).get("opened") == "kiwi",
    ),
    T(
        "search_enter",
        "apps/search.html?enter=1",
        "Search for 'mango' and open the result titled 'Mango guide'.",
        lambda S: (_st(S, "search.html") or {}).get("opened") == "mango",
        trap="enter_only",
    ),
    T(
        "wizard_basic",
        "apps/wizard.html",
        "Complete the signup wizard with the name 'Ada' and the Pro plan.",
        lambda S: (
            bool(_st(S, "wizard.html"))
            and _st(S, "wizard.html")["completed"]
            and _st(S, "wizard.html")["name"] == "Ada"
            and _st(S, "wizard.html")["plan"] == "pro"
        ),
    ),
    T(
        "wizard_terms",
        "apps/wizard.html?terms=1",
        "Complete the signup wizard with the name 'Ada' and the Basic plan.",
        lambda S: (
            bool(_st(S, "wizard.html"))
            and _st(S, "wizard.html")["completed"]
            and _st(S, "wizard.html")["name"] == "Ada"
            and _st(S, "wizard.html")["plan"] == "basic"
        ),
        trap="silent_validation",
    ),
    T(
        "cart_checkout",
        "apps/cart.html",
        "Add a Widget to the cart and check out.",
        lambda S: bool(_order(S)) and "widget" in _order(S)["items"],
    ),
    T(
        "cart_qty",
        "apps/cart.html",
        "Add a Gadget to the cart, set its quantity to 3, and check out.",
        lambda S: bool(_order(S)) and _order(S)["items"].get("gadget") == 3,
    ),
    T(
        "cart_remove",
        "apps/cart.html?prefill=doohickey,widget",
        "Remove the Doohickey from the cart and check out with the remaining items.",
        lambda S: (
            bool(_order(S)) and "doohickey" not in _order(S)["items"] and "widget" in _order(S)["items"]
        ),
    ),
    T(
        "cart_address",
        "apps/cart.html?address=1",
        "Add a Widget to the cart and check out, shipping to '1 Main St'.",
        lambda S: (
            bool(_order(S)) and "widget" in _order(S)["items"] and "main" in _order(S)["address"].lower()
        ),
        trap="disabled_until",
    ),
    T(
        "logs_ack",
        "apps/logs.html",
        "Read the deployment log panel to the end (scroll it to the bottom) and click Acknowledge.",
        lambda S: bool(_st(S, "logs.html")) and _st(S, "logs.html")["acknowledged"],
        trap="scroll_container",
    ),
    T(
        "terms_newtab",
        "apps/cart.html",
        "Open the Terms of service page from the shop and accept the terms there.",
        lambda S: bool(_st(S, "terms.html")) and _st(S, "terms.html")["accepted"],
        trap="new_tab",
    ),
]

# ----------------------------------------------------------------------------- observation
OBSERVE_JS = r"""
() => {
  const esc = s => CSS.escape(s);
  const path = (el) => {
    if (el.id) return '#' + esc(el.id);
    const parts = []; let n = el, depth = 0;
    while (n && n.nodeType === 1 && depth < 10) {
      if (n.id) { parts.unshift('#' + esc(n.id)); break; }
      let part = n.tagName.toLowerCase(); const p = n.parentElement;
      if (p) { let i = 1; for (const s of p.children) { if (s === n) break; if (s.tagName === n.tagName) i++; } part += ':nth-of-type(' + i + ')'; }
      parts.unshift(part); n = p; depth++;
    }
    return parts.join('>');
  };
  const vis = el => { const r = el.getBoundingClientRect(); if (r.width === 0 && r.height === 0) return false; const cs = getComputedStyle(el); return cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const txt = el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const SEL = 'h1,h2,h3,p,label,li,td,th,a[href],button,input,select,textarea,summary,[role=button],[role=tab],[role=link],[role=checkbox],[role=textbox],[contenteditable=true],[role=dialog],dialog[open]';
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    if (!vis(el)) continue;
    if (out.length >= 150) { out.push('... (truncated)'); break; }
    const tag = el.tagName.toLowerCase(); const role = el.getAttribute('role'); let kind = tag;
    if (tag === 'input') kind = el.type === 'checkbox' ? 'checkbox' : el.type === 'radio' ? 'radio' : el.type === 'number' ? 'numberbox' : el.type === 'submit' ? 'button' : 'textbox';
    else if (tag === 'a') kind = 'link'; else if (tag === 'textarea') kind = 'textbox'; else if (role) kind = role; else if (el.isContentEditable) kind = 'textbox';
    if (role === 'dialog' || tag === 'dialog') kind = 'DIALOG';
    let name = (tag === 'input' || tag === 'select' || tag === 'textarea') ? '' : txt(el);
    if (!name && el.labels && el.labels[0]) name = txt(el.labels[0]);
    if (!name) name = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
    if (tag === 'select') name = (name ? name + ' ' : '') + 'options=[' + Array.from(el.options).map(o => o.textContent.trim()).join('|') + ']';
    let s = kind + ' selector="' + path(el) + '"' + (name ? ' "' + name + '"' : '');
    if (tag === 'input' || tag === 'textarea' || tag === 'select') { if (el.type === 'checkbox' || el.type === 'radio') s += ' checked=' + el.checked; else s += ' value="' + String(el.value).slice(0, 40) + '"'; }
    if (el.disabled) s += ' [disabled]'; if (el.readOnly) s += ' [readonly]';
    if (el.getAttribute('aria-selected') === 'true') s += ' [selected]'; if (el.getAttribute('aria-current')) s += ' [current]';
    if (tag === 'a') s += ' href=' + el.getAttribute('href');
    out.push(s);
  }
  const se = document.scrollingElement || document.documentElement;
  return { url: location.href, title: document.title, scrollY: Math.round(window.scrollY), maxScroll: Math.max(0, se.scrollHeight - se.clientHeight), elements: out };
}
"""


def render_observation(obs: dict, tabs: list[dict]) -> str:
    lines = [
        f"URL: {obs['url']}",
        f"TITLE: {obs['title']}",
        "TABS: "
        + "; ".join(
            f"[{t['index']}] {t['url'].split('/')[-1]}{' (current)' if t['current'] else ''}" for t in tabs
        ),
        f"SCROLL: window y={obs['scrollY']} (max {obs['maxScroll']})",
        'ELEMENTS (kind selector="<css>" "label" state):',
    ]
    lines += ["  " + e for e in obs["elements"]]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- model
class LLM:
    def __init__(self, endpoint: str, model: str, seed: int, think: bool, max_tokens: int):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.seed = seed
        self.think = think
        self.max_tokens = max_tokens
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latencies: list[float] = []

    def chat(self, messages: list[dict]) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "seed": self.seed,
            "max_tokens": self.max_tokens,
        }
        if not self.think:
            body["reasoning_effort"] = "none"
        req = urllib.request.Request(
            self.endpoint + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        last = None
        for attempt in range(3):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    r = json.load(resp)
                self.latencies.append(time.perf_counter() - t0)
                self.calls += 1
                u = r.get("usage") or {}
                self.prompt_tokens += u.get("prompt_tokens", 0)
                self.completion_tokens += u.get("completion_tokens", 0)
                return r["choices"][0]["message"].get("content") or ""
            except Exception as e:  # network hiccup: retry
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"model call failed after retries: {last}")


def parse_action(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    for cand in (m.group(0), text):
        try:
            d = json.loads(cand)
            if isinstance(d, dict) and "tool" in d:
                return _normalize(d)
        except Exception:
            continue
    # last resort: first balanced object
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    d = json.loads(text[start : i + 1])
                    if isinstance(d, dict) and "tool" in d:
                        return _normalize(d)
                except Exception:
                    pass
    return None


def _normalize(d: dict) -> dict:
    """Accept {"tool","args":{...}} and the flattened {"tool","selector":...} the model sometimes emits."""
    args = d.get("args")
    if not isinstance(args, dict):
        args = {}
    for k, v in d.items():
        if k not in ("tool", "args", "why") and k not in args:
            args[k] = v
    d["args"] = args
    return d


# ----------------------------------------------------------------------------- MCP + browser
class ReceiptMCP:
    """One action-receipt MCP server process (stdio) attached over CDP to our Chromium."""

    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url
        self._cm = None
        self._sess_cm = None
        self.session = None
        self.receipts: list[dict] = []

    async def __aenter__(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = {
            k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "PLAYWRIGHT_BROWSERS_PATH")
        }
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "action_receipt.server", "--cdp", self.cdp_url],
            cwd=str(PROJECT_DIR),
            env=env,
        )
        self._errlog = open(TRACES / "mcp_server_stderr.log", "a")  # noqa: SIM115 - closed with the client
        self._cm = stdio_client(params, errlog=self._errlog)
        read, write = await self._cm.__aenter__()
        self._sess_cm = ClientSession(read, write)
        self.session = await self._sess_cm.__aenter__()
        await self.session.initialize()
        return self

    async def __aexit__(self, *a):
        try:
            await self._sess_cm.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:
            pass
        self._errlog.close()

    async def call(self, name: str, args: dict) -> dict:
        res = await self.session.call_tool(name, args, read_timeout_seconds=90)
        data: Any = None
        sc = getattr(res, "structuredContent", None)
        if sc:
            data = sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
        else:
            for c in getattr(res, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    try:
                        data = json.loads(t)
                    except Exception:
                        data = {"text": t}
                    break
        if getattr(res, "isError", False):
            return {"error": json.dumps(data)[:400] if data is not None else "tool error"}
        if isinstance(data, dict) and "receipt" in data:
            self.receipts.append(data["receipt"])
        return data if isinstance(data, dict) else {"result": data}


def render_result(tool: str, out: dict, arm: str) -> str:
    """What the model sees after an action. receipt_off: result + error only. receipt_on: + receipt summary."""
    if "error" in out and "receipt" not in out:
        return json.dumps({"tool": tool, "error": out["error"]})
    rec = out.get("receipt")
    shown: dict[str, Any] = {"tool": tool, "result": out.get("result")}
    if rec:
        shown["error"] = rec.get("dispatch", {}).get("error")
    else:
        shown.update({k: v for k, v in out.items() if k != "result"})
    if arm == "receipt_on" and rec:
        d = rec.get("delta", {})
        s = rec.get("settlement", {})
        r = {
            "verdict": rec["verdict"],
            "evidence": rec.get("evidence", [])[:6],
            "hint": rec.get("hint"),
            "settlement": f"settled_by={s.get('settled_by')} elapsed={s.get('elapsed_ms')}ms"
            + (" TIMED_OUT" if s.get("timed_out") else ""),
        }
        if d.get("url_changed"):
            r["url_after"] = d.get("url_after")
        if d.get("new_tabs"):
            r["new_tabs"] = d["new_tabs"]
        if d.get("modals_opened"):
            r["modals_opened"] = d["modals_opened"]
        if d.get("dialogs"):
            r["dialogs"] = d["dialogs"]
        if d.get("value_changed"):
            r["value_after"] = d.get("value_after")
        shown["receipt"] = r
    return json.dumps(shown, ensure_ascii=False)


# ----------------------------------------------------------------------------- episode
async def run_episode(task, arm, run_idx, cfg, srv, ctx_pages, llm, system_prompt) -> dict:
    """ctx_pages: callable -> list of Playwright pages in the shared context (harness CDP client)."""
    url = srv.url(task["app"])
    t_start = time.perf_counter()
    lib = lib_hash()  # the library is edited concurrently; record what this episode's server ran
    trace: list[dict] = []
    steps = 0
    terminal = "MAX_STEPS"
    claim_text = ""
    parse_failures = 0
    calls_before = llm.calls
    async with ReceiptMCP(cfg["cdp_url"]) as mcp:
        out = await mcp.call("open", {"url": url})
        current_url = out.get("receipt", {}).get("after", {}).get("url", url)

        async def observe() -> str:
            pages = ctx_pages()
            page = None
            for p in pages:
                if p.url == current_url:
                    page = p
            if page is None and pages:
                page = pages[-1]
            tabs = [{"index": i, "url": p.url, "current": p == page} for i, p in enumerate(pages)]
            if page is None:
                return "URL: (no page)\nELEMENTS: (none)"
            try:
                obs = await page.evaluate(OBSERVE_JS)
            except Exception as e:
                return f"URL: {page.url}\nOBSERVATION UNAVAILABLE ({type(e).__name__})"
            return render_observation(obs, tabs)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"TASK: {task['goal']}\n\nOBSERVATION:\n{await observe()}"},
        ]
        for step in range(cfg["max_steps"]):
            raw = llm.chat(messages)
            act = parse_action(raw)
            trace.append({"step": step + 1, "model_raw": raw[:2000]})
            if act is None:
                parse_failures += 1
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": 'Your reply was not a single JSON object. Reply with exactly one JSON object: {"tool": ..., "args": {...}}',
                    }
                )
                if parse_failures >= 2:
                    terminal = "ERROR"
                    break
                continue
            tool = str(act.get("tool", "")).lower()
            args = act.get("args") or {}
            steps += 1
            messages.append({"role": "assistant", "content": json.dumps(act)})
            if tool == "done":
                terminal = "DONE"
                claim_text = str(args.get("summary", ""))[:300]
                break
            if tool == "fail":
                terminal = "FAIL"
                claim_text = str(args.get("reason", ""))[:300]
                break
            # map to MCP tools
            try:
                if tool == "click":
                    out = await mcp.call("click", {"selector": str(args.get("selector", ""))})
                elif tool == "type":
                    out = await mcp.call(
                        "type",
                        {
                            "selector": str(args.get("selector", "")),
                            "text": str(args.get("text", "")),
                            "clear": bool(args.get("clear", False)),
                            "submit": bool(args.get("submit", False)),
                        },
                    )
                elif tool == "press":
                    out = await mcp.call(
                        "press",
                        {"key": str(args.get("key", "Enter")), "selector": args.get("selector") or None},
                    )
                elif tool == "scroll":
                    pages_arg = args.get("pages", 1)
                    try:
                        pages_arg = float(pages_arg)
                    except Exception:
                        pages_arg = 1.0
                    out = await mcp.call(
                        "scroll",
                        {
                            "direction": str(args.get("direction", "down")),
                            "pages": pages_arg,
                            "selector": args.get("selector") or None,
                        },
                    )
                elif tool == "navigate":
                    out = await mcp.call("navigate", {"url": str(args.get("url", ""))})
                elif tool == "tabs":
                    sel = args.get("select")
                    out = await mcp.call("tabs", {"select": int(sel)} if sel is not None else {})
                    for t in out.get("tabs", []):
                        if t.get("current"):
                            current_url = t["url"]
                else:
                    out = {"error": f"unknown tool {tool!r}"}
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            rec = out.get("receipt")
            if rec:
                current_url = rec.get("after", {}).get("url", current_url)
                if rec.get("delta", {}).get("new_tabs"):
                    pass  # the agent must switch with tabs(); current stays
            shown = render_result(tool, out, arm)
            obs = await observe()
            trace[-1].update(
                {
                    "tool": tool,
                    "args": args,
                    "shown": shown,
                    "verdict": rec.get("verdict") if rec else None,
                    "receipt_id": rec.get("id") if rec else None,
                }
            )
            # keep the previous and the new observation in full (so the agent can diff them itself,
            # in both arms); elide anything older to keep the context small
            full = [
                m
                for m in messages
                if m["role"] == "user" and "\nOBSERVATION:\n" in m["content"] and not m.get("_elided")
            ]
            for m in full[:-1]:
                m["content"] = (
                    m["content"].split("\nOBSERVATION:\n")[0] + "\nOBSERVATION: (earlier observation elided)"
                )
                m["_elided"] = True
            messages.append({"role": "user", "content": f"RESULT: {shown}\n\nOBSERVATION:\n{obs}"})
        # validator over every open tab
        states = {}
        for p in ctx_pages():
            try:
                states[p.url] = await p.evaluate("window.__state ? window.__state() : null")
            except Exception:
                states[p.url] = None
        try:
            passed = bool(task["check"](states))
        except Exception:
            passed = False
        receipts = list(mcp.receipts)
    # clean up tabs for the next episode
    for p in ctx_pages():
        try:
            await p.close()
        except Exception:
            pass
    clean_msgs = [{k: v for k, v in m.items() if k != "_elided"} for m in messages]
    row = {
        "task": task["id"],
        "trap": task["trap"],
        "arm": arm,
        "run": run_idx,
        "terminal": terminal,
        "claim": claim_text,
        "validator_pass": passed,
        "steps": steps,
        "model_calls": llm.calls - calls_before,
        "parse_failures": parse_failures,
        "wall_s": round(time.perf_counter() - t_start, 1),
        "lib_hash": lib,
        "verdicts": {
            v: sum(1 for r in receipts if r.get("verdict") == v)
            for v in ("changed", "no_op", "navigated", "blocked", "unknown")
        },
        "states": states,
    }
    TRACES.mkdir(parents=True, exist_ok=True)
    (TRACES / f"{task['id']}__{arm}__run{run_idx}.json").write_text(
        json.dumps(
            {"row": row, "trace": trace, "messages": clean_msgs, "receipts": receipts}, indent=1, default=str
        )
    )
    return row


# ----------------------------------------------------------------------------- driver
async def run(cfg) -> list[dict]:
    from playwright.async_api import async_playwright

    tasks = [t for t in TASKS if not cfg["tasks"] or t["id"] in cfg["tasks"]]
    done_keys = set()
    rows: list[dict] = []
    if cfg["resume"] and EPISODES.exists():
        for line in EPISODES.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("model") == cfg["model"] and r.get("think") == cfg["think"]:
                    rows.append(r)
                    done_keys.add((r["task"], r["arm"], r["run"]))
    elif EPISODES.exists():
        EPISODES.unlink()
    TRACES.mkdir(parents=True, exist_ok=True)
    prompts = {arm: (PROMPTS / f"{arm}.txt").read_text() for arm in cfg["arms"]}
    llm = LLM(cfg["endpoint"], cfg["model"], cfg["seed"], cfg["think"], cfg["max_tokens"])
    port = free_port()
    async with FixtureServer() as srv, async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        cdp_url = f"http://127.0.0.1:{port}"
        cfg["cdp_url"] = cdp_url
        cfg["chromium"] = browser.version
        observer = await pw.chromium.connect_over_cdp(cdp_url)
        ctx = observer.contexts[0]
        for run_idx in range(1, cfg["runs"] + 1):
            for task in tasks:
                for arm in cfg["arms"]:
                    if (task["id"], arm, run_idx) in done_keys:
                        continue
                    if llm.calls >= cfg["max_calls"]:
                        log(f"model-call budget {cfg['max_calls']} reached; stopping")
                        break
                    max_steps = cfg["max_steps"]
                    sp = prompts[arm].replace(
                        "You have a limited number of actions", f"You have at most {max_steps} actions"
                    )
                    try:
                        row = await run_episode(
                            task, arm, run_idx, cfg, srv, lambda: list(ctx.pages), llm, sp
                        )
                    except Exception as e:
                        log(f"episode {task['id']} {arm} run{run_idx} crashed: {type(e).__name__}: {e}")
                        row = {
                            "task": task["id"],
                            "trap": task["trap"],
                            "arm": arm,
                            "run": run_idx,
                            "terminal": "ERROR",
                            "claim": f"harness: {type(e).__name__}: {str(e)[:200]}",
                            "validator_pass": False,
                            "steps": 0,
                            "model_calls": 0,
                            "parse_failures": 0,
                            "wall_s": 0,
                            "verdicts": {},
                            "states": {},
                        }
                        for p in list(ctx.pages):
                            try:
                                await p.close()
                            except Exception:
                                pass
                    row.update(
                        {
                            "model": cfg["model"],
                            "think": cfg["think"],
                            "seed": cfg["seed"],
                            "max_steps": cfg["max_steps"],
                        }
                    )
                    rows.append(row)
                    with EPISODES.open("a") as f:
                        f.write(json.dumps(row, default=str) + "\n")
                    log(
                        f"run{run_idx} {task['id']:16s} {arm:11s} -> {row['terminal']:9s} validator={'PASS' if row['validator_pass'] else 'FAIL'} "
                        f"steps={row['steps']} calls={llm.calls} ({row['wall_s']}s)"
                    )
        await observer.close()
        await browser.close()
    cfg["llm_stats"] = {
        "calls": llm.calls,
        "prompt_tokens": llm.prompt_tokens,
        "completion_tokens": llm.completion_tokens,
        "latency_p50_s": round(sorted(llm.latencies)[len(llm.latencies) // 2], 2) if llm.latencies else None,
    }
    return rows


def summarize(rows: list[dict], cfg: dict) -> tuple[dict, str]:
    arms = cfg["arms"]
    tasks = sorted(
        {r["task"] for r in rows},
        key=lambda t: [x["id"] for x in TASKS].index(t) if t in [x["id"] for x in TASKS] else 99,
    )
    runs = sorted({r["run"] for r in rows})
    out = {"arms": {}}
    head = []
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        n = len(rs)
        fails = [r for r in rs if not r["validator_pass"]]
        done_on_fail = [r for r in fails if r["terminal"] == "DONE"]
        dones = [r for r in rs if r["terminal"] == "DONE"]
        pass_on_done = [r for r in dones if r["validator_pass"]]
        vol = [r for r in fails if r["terminal"] in ("DONE", "FAIL")]
        steps = [r["steps"] for r in rs]
        per_run = []
        for k in runs:
            rk = [r for r in rs if r["run"] == k]
            fk = [r for r in rk if not r["validator_pass"]]
            dk = [r for r in fk if r["terminal"] == "DONE"]
            pk = [r for r in rk if r["validator_pass"]]
            per_run.append(f"run{k}: P(DONE|fail)={len(dk)}/{len(fk)}, pass={len(pk)}/{len(rk)}")
        term = {t: sum(1 for r in rs if r["terminal"] == t) for t in ("DONE", "FAIL", "MAX_STEPS", "ERROR")}
        verd = {}
        for r in rs:
            for v, c in r.get("verdicts", {}).items():
                verd[v] = verd.get(v, 0) + c
        a = {
            "n": n,
            "validator_pass": sum(1 for r in rs if r["validator_pass"]),
            "fails": len(fails),
            "done_on_fail": len(done_on_fail),
            "p_done_given_fail": wilson(len(done_on_fail), len(fails)),
            "p_done_given_fail_voluntary": wilson(len(done_on_fail), len(vol)),
            "p_pass_given_done": wilson(len(pass_on_done), len(dones)),
            "terminals": term,
            "mean_steps": mean(steps) if steps else None,
            "sd_steps": stdev(steps) if steps else None,
            "per_run": per_run,
            "verdicts": verd,
            "model_calls": sum(r["model_calls"] for r in rs),
        }
        out["arms"][arm] = a
        head.append(
            [
                arm,
                n,
                fmt_wilson(len(done_on_fail), len(fails)),
                fmt_wilson(len(done_on_fail), len(vol)),
                fmt_wilson(a["validator_pass"], n),
                fmt_wilson(len(pass_on_done), len(dones)),
                f"{a['mean_steps']:.1f} ± {a['sd_steps']:.1f}" if steps else "-",
                f"DONE {term['DONE']} / FAIL {term['FAIL']} / MAX {term['MAX_STEPS']} / ERR {term['ERROR']}",
                "; ".join(per_run),
            ]
        )
    # trap vs non-trap
    trap_rows = []
    for arm in arms:
        for grp, pred in (("trap tasks", lambda r: r["trap"]), ("plain tasks", lambda r: not r["trap"])):
            rs = [r for r in rows if r["arm"] == arm and pred(r)]
            fails = [r for r in rs if not r["validator_pass"]]
            dof = [r for r in fails if r["terminal"] == "DONE"]
            trap_rows.append(
                [
                    arm,
                    grp,
                    len(rs),
                    fmt_wilson(sum(1 for r in rs if r["validator_pass"]), len(rs)),
                    fmt_wilson(len(dof), len(fails)),
                ]
            )
    # per task
    task_rows = []
    for t in tasks:
        tr = next((x for x in TASKS if x["id"] == t), None)
        row = [t, (tr or {}).get("trap") or ""]
        for arm in arms:
            rs = sorted([r for r in rows if r["arm"] == arm and r["task"] == t], key=lambda r: r["run"])
            cells = []
            for r in rs:
                cells.append(f"{'P' if r['validator_pass'] else 'F'}/{r['terminal'][:4]}/{r['steps']}")
            row.append(", ".join(cells) if cells else "-")
        task_rows.append(row)
    stats = cfg.get("llm_stats", {})
    md = [
        "# Agent-in-the-loop false completion",
        "",
        f"Model `{cfg['model']}` at `{cfg['endpoint']}` (thinking {'on' if cfg['think'] else 'off'}, temperature 0, seed {cfg['seed']}), "
        f"{len(tasks)} tasks × {len(runs)} runs × {len(arms)} arms = {len(rows)} episodes, step cap {cfg['max_steps']}. "
        f"Model calls: {stats.get('calls', sum(r['model_calls'] for r in rows))}; tokens in/out: {stats.get('prompt_tokens', '?')}/{stats.get('completion_tokens', '?')}; "
        f"median call latency {stats.get('latency_p50_s', '?')} s.",
        "",
        "## Headline",
        "",
        "P(DONE | validator fail) is the false-completion rate: of the episodes the validator scored as failed, the fraction in which the agent's terminal claim was DONE. "
        "A14 measured 53.0% [52.3, 53.6] pooled on OSWorld-Verified (34,437 scored feasible task-runs from 91 public runs; 76.4% when the agent stopped voluntarily).",
        "",
        md_table(
            [
                "arm",
                "episodes",
                "P(DONE | fail) [Wilson 95%]",
                "P(DONE | fail, voluntary stop)",
                "task success",
                "P(pass | DONE)",
                "mean steps ± sd",
                "terminals",
                "per-run spread",
            ],
            head,
        ),
        "",
        "## Trap vs plain tasks",
        "",
        md_table(["arm", "group", "episodes", "task success", "P(DONE | fail)"], trap_rows),
        "",
        "## Per task (cell = validator P/F / terminal / steps, one entry per run)",
        "",
        md_table(["task", "trap"] + arms, task_rows),
        "",
        "## Receipt verdicts seen per arm (the server computes them in both arms; only receipt_on shows them to the model)",
        "",
        md_table(
            ["arm"] + ["changed", "no_op", "navigated", "blocked", "unknown"],
            [
                [arm]
                + [
                    out["arms"][arm]["verdicts"].get(v, 0)
                    for v in ("changed", "no_op", "navigated", "blocked", "unknown")
                ]
                for arm in arms
            ],
        ),
        "",
    ]
    return out, "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--model", default="qwen3.6:35b-a3b")
    ap.add_argument("--endpoint", default="http://100.127.85.58:11434/v1")
    ap.add_argument("--seed", type=int, default=20260902)
    ap.add_argument(
        "--think", action="store_true", help="leave the model's thinking on (default: reasoning_effort=none)"
    )
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--arms", default="receipt_off,receipt_on")
    ap.add_argument("--tasks", default="", help="comma-separated task ids (default all)")
    ap.add_argument("--max-calls", type=int, default=900)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--summarize-only", action="store_true")
    args = ap.parse_args(argv)
    cfg = {
        "runs": args.runs,
        "max_steps": args.max_steps,
        "model": args.model,
        "endpoint": args.endpoint,
        "seed": args.seed,
        "think": args.think,
        "max_tokens": args.max_tokens if not args.think else max(args.max_tokens, 2000),
        "arms": args.arms.split(","),
        "tasks": [t for t in args.tasks.split(",") if t],
        "max_calls": args.max_calls,
        "resume": args.resume,
    }
    if args.summarize_only:
        rows = [json.loads(l) for l in EPISODES.read_text().splitlines() if l.strip()]
        rows = [r for r in rows if r.get("model") == cfg["model"] and r.get("think") == cfg["think"]]
    else:
        rows = asyncio.run(run(cfg))
    summary, md = summarize(rows, cfg)
    info = env_info(
        {
            "chromium": cfg.get("chromium"),
            "model": cfg["model"],
            "model_endpoint": cfg["endpoint"],
            "think": cfg["think"],
            "seed": cfg["seed"],
            "max_steps": cfg["max_steps"],
            "runs": cfg["runs"],
            "llm_stats": cfg.get("llm_stats"),
        }
    )
    md += "\n## Environment\n\n" + env_md(info) + "\n"
    jp, mp = write_results("agent_loop", {"env": info, "summary": summary, "episodes": rows}, md)
    log(f"wrote {jp} and {mp}")
    print(md)


if __name__ == "__main__":
    main()
