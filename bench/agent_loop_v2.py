"""Bench 4 v2 - agent-in-the-loop false completion with a harder task set, three arms, two models.

    python -m bench.agent_loop_v2 [--set hard|legacy|all] [--models qwen3.6:35b-a3b,qwen3.5:4b]
                                  [--seeds 20260902,20260903] [--runs 1] [--max-steps 12] [--temperature 0.3]
                                  [--arms receipt_off,receipt_on,receipt_enforced] [--tasks id,...]
                                  [--max-calls 1300] [--resume] [--summarize-only] [--endpoint URL]

v1 (``bench/agent_loop.py``) was a null and under-powered: the 35B solved 95-97% of 22 tasks, so
P(DONE | validator fail) rested on 2-3 episodes per arm, and temperature 0 with one seed made the
runs near-identical. v2 keeps the same agent, observation and MCP path and changes four things:

* **36 harder tasks** (``bench/tasks/``; the 22 v1 tasks are kept as the ``legacy`` set) whose
  traps a receipt can see: consent banners covering controls, silent and native validation,
  delayed enables, virtualised inner scroll lists, a load-on-scroll container, in-page confirm
  dialogs, Next-only pagination, Enter-only inputs, a new tab, a toggle that reverts on blur, an
  autosave that fires only after idle, a modal overlay, an optimistic UI that reverts on a 4xx.
* **three arms**: ``receipt_off`` (raw results), ``receipt_on`` (verdict + hint shown), and
  ``receipt_enforced`` - the product claim: a deterministic policy in the harness that *refuses*
  ``done`` when the last action's verdict was ``no_op``/``blocked``/``unknown`` (or nothing has
  taken effect yet), returns the hint, and forces another step; at most 2 refusals per episode,
  then the claim is accepted. No model is involved in the policy.
* **two models** (``qwen3.6:35b-a3b`` and the weaker ``qwen3.5:4b``), temperature 0.3, **two
  seeds**; ``--runs N`` adds N-1 further seeds per listed seed (a fixed Ollama seed makes a repeat
  run a replay, so a second "run" only carries information if it samples differently).
* step cap 12; the validator is also read at every refusal, so each refusal is classed as
  justified (validator failing at that moment) or spurious (task already complete).

Every episode row records ``lib_hash``; the two models write separate episode files so they can
run concurrently (``agent_loop_v2_episodes__<model>.jsonl``); ``--summarize-only`` merges them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from ._common import (
    BENCH_DIR,
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
from .agent_loop import OBSERVE_JS as _OBSERVE_JS_V1
from .agent_loop import ReceiptMCP, parse_action, render_observation, render_result
from .tasks import BY_ID
from .tasks import select as select_tasks

PROMPTS = BENCH_DIR / "prompts"
TRACES = RESULTS / "agent_loop_v2_traces"
ARMS = ("receipt_off", "receipt_on", "receipt_enforced")
EFFECTIVE = ("changed", "navigated")
NON_EFFECTIVE = ("no_op", "blocked", "unknown")
MAX_REFUSALS = 2

# v2 observation: v1's listing plus live-status text (role=status / <output>), which the new apps use for
# "Saved" / "Pending" / "Rejected" messages so a raw-result agent can read them too.
OBSERVE_JS = _OBSERVE_JS_V1.replace(
    "[role=dialog],dialog[open]", "[role=dialog],dialog[open],[role=status],output"
)
# ... and scrollable containers, listed first as `scrollable selector="#list" "label" (scrollTop=0 max=1560)`, so that an
# inner scroll box is *perceivable* in every arm; whether a scroll had an effect is what the receipt adds.
_CONTAINERS_JS = r"""
  const boxes = [];
  for (const el of document.querySelectorAll('*')) {
    if (el === document.documentElement || el === document.body) continue;
    const cs = getComputedStyle(el);
    if (!/(auto|scroll)/.test(cs.overflowY) || el.scrollHeight <= el.clientHeight + 2 || !vis(el)) continue;
    const lab = el.getAttribute('aria-label') || '';
    boxes.push('scrollable selector="' + path(el) + '"' + (lab ? ' "' + lab + '"' : '') + ' (scrollTop=' + Math.round(el.scrollTop) + ' max=' + (el.scrollHeight - el.clientHeight) + ')');
    if (boxes.length >= 5) break;
  }
  out.unshift(...boxes);
  const se = document.scrollingElement || document.documentElement;"""
assert OBSERVE_JS.count("  const se = document.scrollingElement || document.documentElement;") == 1
OBSERVE_JS = OBSERVE_JS.replace(
    "  const se = document.scrollingElement || document.documentElement;", _CONTAINERS_JS
)
# checkVisibility() also excludes content inside a closed <details> (content-visibility: hidden), which the v1 rect test lists as visible
_VIS_V1 = "const vis = el => { const r = el.getBoundingClientRect(); if (r.width === 0 && r.height === 0) return false; const cs = getComputedStyle(el); return cs.visibility !== 'hidden' && cs.display !== 'none'; };"
_VIS_V2 = (
    "const vis = el => { if (el.checkVisibility && !el.checkVisibility({contentVisibilityAuto: true, visibilityProperty: true})) return false; "
    "const r = el.getBoundingClientRect(); if (r.width === 0 && r.height === 0) return false; const cs = getComputedStyle(el); return cs.visibility !== 'hidden' && cs.display !== 'none'; };"
)
assert OBSERVE_JS.count(_VIS_V1) == 1
OBSERVE_JS = OBSERVE_JS.replace(_VIS_V1, _VIS_V2)


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", model)


def episodes_path(model: str) -> Path:
    return RESULTS / f"agent_loop_v2_episodes__{model_slug(model)}.jsonl"


# ----------------------------------------------------------------------------- model
class LLM:
    def __init__(
        self, endpoint: str, model: str, seed: int, think: bool, max_tokens: int, temperature: float
    ):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.seed = seed
        self.think = think
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.latencies: list[float] = []

    def chat(self, messages: list[dict]) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
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
            except Exception as e:
                last = e
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"model call failed after retries: {last}")


# ----------------------------------------------------------------------------- policy
_SHOT_RE = re.compile(r"screenshot_changed: ([0-9.]+) of pixels, dhash distance (\d+)")


def effective_verdict(rec: dict | None) -> str | None:
    """The verdict the policy keys on. Equal to the receipt's verdict except for one bench-side
    workaround (FINDINGS F9): a `changed` whose only evidence is a screenshot delta at the noise
    floor (< 1% of pixels *and* dhash distance 0 - e.g. a button's active-state paint on a
    `press` that had no handler) is treated as `no_op`. Recorded per action as ``weak_visual``."""
    if not rec:
        return None
    v = rec.get("verdict")
    if v != "changed":
        return v
    ev = [
        e
        for e in rec.get("evidence", [])
        if not e.startswith(("scrolled_into_view_before_capture", "focus_changed"))
    ]
    shots = [m for e in ev for m in [_SHOT_RE.match(e)] if m]
    if ev and len(shots) == len(ev) and all(float(m.group(1)) < 0.01 and int(m.group(2)) == 0 for m in shots):
        return "weak_visual"
    return v


def policy_reason(verdict_history: list[str | None]) -> str | None:
    """The deterministic DONE gate. Returns why DONE should be refused, or None to accept.

    Rule 1: the last action's verdict was no_op / blocked / unknown / weak_visual (or the action errored out).
    Rule 2: no action in the episode has had an effect (changed / navigated) at all.
    """
    if not verdict_history:
        return "no action has been taken yet"
    last = verdict_history[-1]
    if last is None or last in NON_EFFECTIVE or last == "weak_visual":
        return f"the last action's receipt verdict was {last or 'error'}" + (
            " (a screenshot-only change at the noise floor)" if last == "weak_visual" else ""
        )
    if not any(v in EFFECTIVE for v in verdict_history):
        return "no action has taken effect yet (no changed/navigated receipt in this episode)"
    return None


def render_refusal(reason: str, last_hint: str | None, refusals: int) -> str:
    return json.dumps(
        {
            "tool": "done",
            "refused": True,
            "reason": reason,
            "hint": last_hint
            or "re-observe the page, act on the part of the task that has not taken effect, and verify it changed",
            "instruction": f"done was refused ({refusals}/{MAX_REFUSALS}): the task is not complete while the last action had no effect. "
            "Take another action that moves the task forward, confirm its receipt says changed or navigated, then call done again - or call fail.",
        },
        ensure_ascii=False,
    )


# ----------------------------------------------------------------------------- episode
async def run_episode(task, arm, seed, run_idx, cfg, srv, ctx_pages, llm, system_prompt) -> dict:
    url = srv.url(task["app"])
    t_start = time.perf_counter()
    lib = lib_hash()
    trace: list[dict] = []
    steps = actions = 0
    terminal = "MAX_STEPS"
    claim_text = ""
    parse_failures = 0
    refusals = 0
    refusal_details: list[dict] = []
    done_attempts = 0
    policy_would_refuse = None
    accepted_after_cap = False
    verdict_history: list[str | None] = []
    last_hint: str | None = None
    weak_visual = 0
    calls_before, ptok_before, ctok_before = llm.calls, llm.prompt_tokens, llm.completion_tokens
    enforced = arm == "receipt_enforced"

    async def validate() -> tuple[bool, dict]:
        states = {}
        for p in ctx_pages():
            try:
                states[p.url] = await p.evaluate("window.__state ? window.__state() : null")
            except Exception:
                states[p.url] = None
        try:
            ok = bool(task["check"](states))
        except Exception:
            ok = False
        return ok, states

    async with ReceiptMCP(cfg["cdp_url"]) as mcp:
        out = await mcp.call("open", {"url": url})
        current_url = ((out.get("receipt") or {}).get("after") or {}).get("url") or url

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

        def elide_old_observations():
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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"TASK: {task['goal']}\n\nOBSERVATION:\n{await observe()}"},
        ]
        for turn in range(cfg["max_steps"]):
            raw = llm.chat(messages)
            act = parse_action(raw)
            trace.append({"turn": turn + 1, "model_raw": raw[:2000]})
            if act is None:
                parse_failures += 1
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": 'Your reply was not a single JSON object. Reply with exactly one JSON object: {"tool": ..., "args": {...}}',
                    }
                )
                if (
                    parse_failures >= 3
                ):  # v1 allowed 2; at temperature 0.3 a single garbled reply is sampling noise, not a stuck agent
                    terminal = "ERROR"
                    break
                continue
            tool = str(act.get("tool", "")).lower()
            args = act.get("args") or {}
            steps += 1
            messages.append({"role": "assistant", "content": json.dumps(act)})
            if tool == "done":
                done_attempts += 1
                reason = policy_reason(verdict_history)
                if enforced and reason and refusals < MAX_REFUSALS:
                    refusals += 1
                    ok_now, _ = await validate()
                    refusal_details.append(
                        {
                            "turn": turn + 1,
                            "reason": reason,
                            "last_verdict": verdict_history[-1] if verdict_history else None,
                            "validator_pass_at_refusal": ok_now,
                        }
                    )
                    shown = render_refusal(reason, last_hint, refusals)
                    trace[-1].update(
                        {
                            "tool": "done",
                            "args": args,
                            "refused": True,
                            "reason": reason,
                            "validator_pass_at_refusal": ok_now,
                            "shown": shown,
                        }
                    )
                    elide_old_observations()
                    messages.append(
                        {"role": "user", "content": f"RESULT: {shown}\n\nOBSERVATION:\n{await observe()}"}
                    )
                    continue
                terminal = "DONE"
                claim_text = str(args.get("summary", ""))[:300]
                policy_would_refuse = bool(reason)
                accepted_after_cap = enforced and bool(reason)
                trace[-1].update(
                    {"tool": "done", "args": args, "policy_would_refuse": bool(reason), "reason": reason}
                )
                break
            if tool == "fail":
                terminal = "FAIL"
                claim_text = str(args.get("reason", ""))[:300]
                trace[-1].update({"tool": "fail", "args": args})
                break
            actions += 1
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
                elif tool == "select":
                    out = await mcp.call(
                        "select",
                        {"selector": str(args.get("selector", "")), "value": str(args.get("value", ""))},
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
                current_url = (rec.get("after") or {}).get("url") or current_url
                verdict_history.append(effective_verdict(rec))
                if verdict_history[-1] == "weak_visual":
                    weak_visual += 1
                last_hint = rec.get("hint") or last_hint
            elif tool == "tabs" and "error" not in out:
                pass  # a tab switch is neither effective nor a failure; it does not move the policy's window
            else:
                verdict_history.append(None)
            shown = render_result(tool, out, "receipt_on" if enforced else arm)
            obs = await observe()
            trace[-1].update(
                {
                    "tool": tool,
                    "args": args,
                    "shown": shown,
                    "verdict": rec.get("verdict") if rec else None,
                    "policy_verdict": verdict_history[-1] if rec else None,
                    "receipt_id": rec.get("id") if rec else None,
                }
            )
            elide_old_observations()
            messages.append({"role": "user", "content": f"RESULT: {shown}\n\nOBSERVATION:\n{obs}"})
        passed, states = await validate()
        receipts = list(mcp.receipts)
    for p in ctx_pages():
        try:
            await p.close()
        except Exception:
            pass
    clean_msgs = [{k: v for k, v in m.items() if k != "_elided"} for m in messages]
    row = {
        "task": task["id"],
        "set": task["set"],
        "trap": task["trap"],
        "arm": arm,
        "model": llm.model,
        "seed": seed,
        "run": run_idx,
        "terminal": terminal,
        "claim": claim_text,
        "validator_pass": passed,
        "steps": steps,
        "actions": actions,
        "model_calls": llm.calls - calls_before,
        "prompt_tokens": llm.prompt_tokens - ptok_before,
        "completion_tokens": llm.completion_tokens - ctok_before,
        "parse_failures": parse_failures,
        "wall_s": round(time.perf_counter() - t_start, 1),
        "lib_hash": lib,
        "verdicts": {
            v: sum(1 for r in receipts if r.get("verdict") == v)
            for v in ("changed", "no_op", "navigated", "blocked", "unknown")
        },
        "last_verdict": verdict_history[-1] if verdict_history else None,
        "weak_visual": weak_visual,
        "done_attempts": done_attempts,
        "policy_would_refuse": policy_would_refuse,
        "refusals": refusals,
        "refusal_details": refusal_details,
        "accepted_after_cap": accepted_after_cap,
        "states": states,
    }
    TRACES.mkdir(parents=True, exist_ok=True)
    (TRACES / f"{task['id']}__{arm}__{model_slug(llm.model)}__s{seed}__r{run_idx}.json").write_text(
        json.dumps(
            {"row": row, "trace": trace, "messages": clean_msgs, "receipts": receipts}, indent=1, default=str
        )
    )
    return row


# ----------------------------------------------------------------------------- driver
def load_rows(models: list[str] | None = None) -> list[dict]:
    rows = []
    for p in sorted(RESULTS.glob("agent_loop_v2_episodes__*.jsonl")):
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if not models or r.get("model") in models:
                    rows.append(r)
    return rows


async def run(cfg) -> list[dict]:
    from playwright.async_api import async_playwright

    model = cfg["model"]
    tasks = select_tasks(cfg["set"], cfg["tasks"])
    ep_path = episodes_path(model)
    done_keys = set()
    rows: list[dict] = []
    if cfg["resume"] and ep_path.exists():
        for r in load_rows([model]):
            rows.append(r)
            done_keys.add((r["task"], r["arm"], r["seed"], r["run"]))
    elif ep_path.exists():
        ep_path.unlink()
    TRACES.mkdir(parents=True, exist_ok=True)
    prompts = {
        "receipt_off": (PROMPTS / "receipt_off_v2.txt").read_text(),
        "receipt_on": (PROMPTS / "receipt_on_v2.txt").read_text(),
        "receipt_enforced": (PROMPTS / "receipt_enforced.txt").read_text(),
    }
    llm = LLM(cfg["endpoint"], model, cfg["seeds"][0], cfg["think"], cfg["max_tokens"], cfg["temperature"])
    port = free_port()
    stopped = False
    async with FixtureServer() as srv, async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[f"--remote-debugging-port={port}"])
        cdp_url = f"http://127.0.0.1:{port}"
        cfg["cdp_url"] = cdp_url
        cfg["chromium"] = browser.version
        observer = await pw.chromium.connect_over_cdp(cdp_url)
        ctx = observer.contexts[0]
        for run_idx in range(1, cfg["runs"] + 1):
            for base_seed in cfg["seeds"]:
                seed = base_seed + 1000 * (
                    run_idx - 1
                )  # a repeat run must sample differently or it is a replay
                for task in tasks:
                    for arm in cfg["arms"]:
                        if (task["id"], arm, seed, run_idx) in done_keys:
                            continue
                        if llm.calls >= cfg["max_calls"]:
                            if not stopped:
                                log(f"model-call budget {cfg['max_calls']} reached; stopping")
                            stopped = True
                            break
                        llm.seed = seed
                        sp = prompts[arm].replace(
                            "You have a limited number of actions",
                            f"You have at most {cfg['max_steps']} actions",
                        )
                        try:
                            row = await run_episode(
                                task, arm, seed, run_idx, cfg, srv, lambda: list(ctx.pages), llm, sp
                            )
                        except Exception as e:
                            import traceback

                            log(
                                f"episode {task['id']} {arm} s{seed} r{run_idx} crashed: {type(e).__name__}: {e}\n"
                                + "".join(traceback.format_exception(e)[-4:])
                            )
                            row = {
                                "task": task["id"],
                                "set": task["set"],
                                "trap": task["trap"],
                                "arm": arm,
                                "model": model,
                                "seed": seed,
                                "run": run_idx,
                                "terminal": "ERROR",
                                "claim": f"harness: {type(e).__name__}: {str(e)[:200]}",
                                "validator_pass": False,
                                "steps": 0,
                                "actions": 0,
                                "model_calls": 0,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "parse_failures": 0,
                                "wall_s": 0,
                                "lib_hash": lib_hash(),
                                "verdicts": {},
                                "last_verdict": None,
                                "done_attempts": 0,
                                "policy_would_refuse": None,
                                "refusals": 0,
                                "refusal_details": [],
                                "accepted_after_cap": False,
                                "states": {},
                            }
                            for p in list(ctx.pages):
                                try:
                                    await p.close()
                                except Exception:
                                    pass
                        row.update(
                            {
                                "think": cfg["think"],
                                "temperature": cfg["temperature"],
                                "max_steps": cfg["max_steps"],
                                "tag": cfg["tag"],
                            }
                        )
                        rows.append(row)
                        with ep_path.open("a") as f:
                            f.write(json.dumps(row, default=str) + "\n")
                        log(
                            f"{model_slug(model)} s{seed} r{run_idx} {task['id']:24s} {arm:16s} -> {row['terminal']:9s} "
                            f"validator={'PASS' if row['validator_pass'] else 'FAIL'} steps={row['steps']} refusals={row['refusals']} "
                            f"calls={llm.calls} ({row['wall_s']}s)"
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


# ----------------------------------------------------------------------------- summary
def _arm_stats(rs: list[dict]) -> dict:
    n = len(rs)
    fails = [r for r in rs if not r["validator_pass"]]
    dof = [r for r in fails if r["terminal"] == "DONE"]
    dones = [r for r in rs if r["terminal"] == "DONE"]
    pod = [r for r in dones if r["validator_pass"]]
    vol = [r for r in fails if r["terminal"] in ("DONE", "FAIL")]
    steps = [r["steps"] for r in rs]
    term = {t: sum(1 for r in rs if r["terminal"] == t) for t in ("DONE", "FAIL", "MAX_STEPS", "ERROR")}
    verd: dict[str, int] = {}
    for r in rs:
        for v, c in r.get("verdicts", {}).items():
            verd[v] = verd.get(v, 0) + c
    refusals = sum(r.get("refusals", 0) for r in rs)
    ref_eps = [r for r in rs if r.get("refusals", 0)]
    justified = sum(
        1 for r in rs for d in r.get("refusal_details", []) if not d.get("validator_pass_at_refusal")
    )
    spurious = sum(1 for r in rs for d in r.get("refusal_details", []) if d.get("validator_pass_at_refusal"))
    ref_pass = sum(1 for r in ref_eps if r["validator_pass"])
    ref_fail_claim = sum(1 for r in ref_eps if r["terminal"] == "FAIL")
    ref_done_fail = sum(1 for r in ref_eps if r["terminal"] == "DONE" and not r["validator_pass"])
    ref_max = sum(1 for r in ref_eps if r["terminal"] == "MAX_STEPS")
    # justified refusals that ended in a pass: the refusal happened while the task was failing and the episode ended passing
    rescued = sum(
        1
        for r in ref_eps
        if r["validator_pass"] and any(not d.get("validator_pass_at_refusal") for d in r["refusal_details"])
    )
    cap = sum(1 for r in rs if r.get("accepted_after_cap"))
    cap_on_fail = sum(
        1 for r in dof if r.get("accepted_after_cap")
    )  # false DONEs the gate refused twice and then let through
    weak = sum(r.get("weak_visual", 0) for r in rs)
    # counterfactual: DONE-on-fail episodes whose terminal DONE the policy would have refused
    catchable = sum(1 for r in dof if r.get("policy_would_refuse"))
    return {
        "n": n,
        "validator_pass": n - len(fails),
        "fails": len(fails),
        "done_on_fail": len(dof),
        "p_done_given_fail": wilson(len(dof), len(fails)),
        "p_done_given_fail_voluntary": wilson(len(dof), len(vol)),
        "p_pass_given_done": wilson(len(pod), len(dones)),
        "terminals": term,
        "mean_steps": mean(steps) if steps else None,
        "sd_steps": stdev(steps) if steps else None,
        "verdicts": verd,
        "model_calls": sum(r["model_calls"] for r in rs),
        "refusals": refusals,
        "episodes_with_refusal": len(ref_eps),
        "refusals_justified": justified,
        "refusals_spurious": spurious,
        "refused_episodes_passed": ref_pass,
        "refused_episodes_rescued": rescued,
        "refused_episodes_claimed_fail": ref_fail_claim,
        "refused_episodes_done_on_fail": ref_done_fail,
        "refused_episodes_max_steps": ref_max,
        "accepted_after_cap": cap,
        "done_on_fail_policy_would_refuse": catchable,
        "voluntary_fails": len(vol),
        "dones": len(dones),
        "weak_visual": weak,
        "accepted_after_cap_on_fail": cap_on_fail,
        "p_done_given_fail_no_cap": wilson(len(dof) - cap_on_fail, len(fails)),
    }


def summarize(rows: list[dict], cfg: dict) -> tuple[dict, str]:
    arms = [a for a in ARMS if any(r["arm"] == a for r in rows)]
    models = sorted({r["model"] for r in rows}, key=lambda m: (m != "qwen3.6:35b-a3b", m))
    sets = [s for s in ("hard", "legacy") if any(r.get("set") == s for r in rows)]
    out: dict[str, Any] = {"models": {}, "n_rows": len(rows)}
    head, cf_rows, enf_rows, set_rows = [], [], [], []
    for m in models:
        out["models"][m] = {}
        for arm in arms:
            rs = [r for r in rows if r["model"] == m and r["arm"] == arm]
            if not rs:
                continue
            a = _arm_stats(rs)
            out["models"][m][arm] = a
            seeds = sorted({r["seed"] for r in rs})
            per_seed = []
            for s in seeds:
                rk = [r for r in rs if r["seed"] == s]
                fk = [r for r in rk if not r["validator_pass"]]
                dk = [r for r in fk if r["terminal"] == "DONE"]
                per_seed.append(
                    f"s{s}: {len(dk)}/{len(fk)}, pass {sum(1 for r in rk if r['validator_pass'])}/{len(rk)}"
                )
            head.append(
                [
                    m,
                    arm,
                    a["n"],
                    fmt_wilson(a["done_on_fail"], a["fails"]),
                    fmt_wilson(a["done_on_fail"], a["voluntary_fails"]),
                    fmt_wilson(a["validator_pass"], a["n"]),
                    fmt_wilson(
                        sum(1 for r in rs if r["terminal"] == "DONE" and r["validator_pass"]), a["dones"]
                    ),
                    f"{a['mean_steps']:.1f} ± {a['sd_steps']:.1f}",
                    f"DONE {a['terminals']['DONE']} / FAIL {a['terminals']['FAIL']} / MAX {a['terminals']['MAX_STEPS']} / ERR {a['terminals']['ERROR']}",
                    "; ".join(per_seed),
                ]
            )
            if arm == "receipt_enforced":
                enf_rows.append(
                    [
                        m,
                        a["refusals"],
                        a["episodes_with_refusal"],
                        a["refusals_justified"],
                        a["refusals_spurious"],
                        a["refused_episodes_passed"],
                        a["refused_episodes_rescued"],
                        a["refused_episodes_claimed_fail"],
                        a["refused_episodes_done_on_fail"],
                        a["refused_episodes_max_steps"],
                        a["accepted_after_cap"],
                        fmt_wilson(a["done_on_fail"] - a["accepted_after_cap_on_fail"], a["fails"]),
                    ]
                )
            else:
                cf_rows.append(
                    [
                        m,
                        arm,
                        a["done_on_fail"],
                        a["done_on_fail_policy_would_refuse"],
                        fmt_wilson(a["done_on_fail_policy_would_refuse"], a["done_on_fail"]),
                    ]
                )
            for s in sets:
                rss = [r for r in rs if r.get("set") == s]
                if rss:
                    b = _arm_stats(rss)
                    set_rows.append(
                        [
                            m,
                            arm,
                            s,
                            b["n"],
                            fmt_wilson(b["validator_pass"], b["n"]),
                            fmt_wilson(b["done_on_fail"], b["fails"]),
                            f"{b['mean_steps']:.1f}",
                            b["refusals"],
                        ]
                    )
    # per trap, per model: failure rate per arm and DONE-on-fail per arm
    trap_rows = []
    traps = sorted({r["trap"] or "(none)" for r in rows}, key=lambda t: (t == "(none)", t))
    for m in models:
        for t in traps:
            row = [m, t]
            any_data = False
            for arm in arms:
                rs = [r for r in rows if r["model"] == m and r["arm"] == arm and (r["trap"] or "(none)") == t]
                if not rs:
                    row.append("-")
                    continue
                any_data = True
                f = [r for r in rs if not r["validator_pass"]]
                d = [r for r in f if r["terminal"] == "DONE"]
                row.append(f"fail {len(f)}/{len(rs)}, DONE&fail {len(d)}")
            if any_data:
                trap_rows.append(row)
    # per task, per model: cell per arm = one entry per (seed, run): P/F + terminal + steps (+Rn refusals)
    task_tables = {}
    order = list(BY_ID)
    for m in models:
        trs = []
        for t in sorted(
            {r["task"] for r in rows if r["model"] == m}, key=lambda t: order.index(t) if t in order else 999
        ):
            row = [t, (BY_ID.get(t) or {}).get("trap") or ""]
            for arm in arms:
                rs = sorted(
                    [r for r in rows if r["model"] == m and r["arm"] == arm and r["task"] == t],
                    key=lambda r: (r["seed"], r["run"]),
                )
                cells = [
                    f"{'P' if r['validator_pass'] else 'F'}/{r['terminal'][:4]}/{r['steps']}"
                    + (f"/R{r['refusals']}" if r.get("refusals") else "")
                    for r in rs
                ]
                row.append(", ".join(cells) if cells else "-")
            trs.append(row)
        task_tables[m] = trs
    stats = cfg.get("llm_stats") or {}
    calls_total = sum(r["model_calls"] for r in rows)
    seeds_all = sorted({r["seed"] for r in rows})
    lib_hashes = sorted({r.get("lib_hash", "?") for r in rows})
    md = [
        "# Agent-in-the-loop false completion, v2 (harder tasks, three arms, two models)",
        "",
        f"Models {', '.join(f'`{m}`' for m in models)} at `{cfg['endpoint']}` (thinking off, temperature {cfg['temperature']}, seeds {seeds_all}); "
        f"task sets {sets}; arms {arms}; {len(rows)} episodes; step cap {cfg['max_steps']}; refusal cap {MAX_REFUSALS} per episode. "
        f"Model calls in the result: {calls_total}. lib_hash per episode: {', '.join(lib_hashes)}.",
        "",
        "## Headline: P(DONE | validator fail) per model and arm",
        "",
        "Of the episodes the validator scored as failed, the fraction in which the agent's terminal claim was DONE (Wilson 95%). "
        "A14 measured 53.0% [52.3, 53.6] pooled on OSWorld-Verified (76.4% when the agent stopped voluntarily). "
        "`receipt_enforced` = `receipt_on` plus the harness refusing DONE while the last receipt is no_op/blocked/unknown (max 2 refusals, then accepted).",
        "",
        md_table(
            [
                "model",
                "arm",
                "episodes",
                "P(DONE | fail)",
                "P(DONE | fail, voluntary stop)",
                "task success",
                "P(pass | DONE)",
                "mean steps ± sd",
                "terminals",
                "per-seed DONE&fail/fail, pass",
            ],
            head,
        ),
        "",
        "## Enforcement: what the refusals did (`receipt_enforced`)",
        "",
        "A refusal is *justified* when the validator was failing at the moment of the refusal and *spurious* when the task was already complete. "
        "*Rescued* = episodes with a justified refusal that ended with the validator passing.",
        "",
        md_table(
            [
                "model",
                "refusals",
                "episodes refused",
                "justified",
                "spurious",
                "refused → passed",
                "rescued",
                "refused → claimed FAIL",
                "refused → DONE on fail",
                "refused → MAX_STEPS",
                "DONE accepted at cap",
                "P(DONE | fail) if the cap were removed",
            ],
            enf_rows,
        ),
        "",
        "*P(DONE | fail) if the cap were removed* recounts the false DONEs that were accepted only because the refusal cap (2) was reached as non-DONE terminals — the agent had already been refused twice on that same claim, so without the cap those episodes end in MAX_STEPS or FAIL. Deterministic recount, not a re-run.",
        "",
        "## Enforcement ceiling in the other arms (counterfactual)",
        "",
        "Of the DONE-on-fail episodes in `receipt_off` / `receipt_on`, how many ended on a receipt the policy would have refused. "
        "This bounds what the last-verdict rule can catch: a false DONE after a `changed` action (e.g. a toggle that took effect but was never applied) passes the gate.",
        "",
        md_table(["model", "arm", "DONE on fail", "policy would refuse", "share"], cf_rows),
        "",
        "## By task set",
        "",
        md_table(
            ["model", "arm", "set", "episodes", "task success", "P(DONE | fail)", "mean steps", "refusals"],
            set_rows,
        ),
        "",
        "## Per trap (cell = validator fails / episodes, and how many of the fails were claimed DONE)",
        "",
        md_table(["model", "trap"] + arms, trap_rows),
        "",
    ]
    for m in models:
        md += [
            f"## Per task — `{m}` (cell = validator P/F / terminal / steps[/R refusals], one entry per seed)",
            "",
            md_table(["task", "trap"] + arms, task_tables[m]),
            "",
        ]
    md += [
        "## Receipt verdicts seen per model and arm (computed in every arm; shown to the model only in receipt_on / receipt_enforced)",
        "",
        md_table(
            [
                "model",
                "arm",
                "changed",
                "no_op",
                "navigated",
                "blocked",
                "unknown",
                "of which weak_visual (F9)",
            ],
            [
                [m, arm]
                + [
                    out["models"][m][arm]["verdicts"].get(v, 0)
                    for v in ("changed", "no_op", "navigated", "blocked", "unknown")
                ]
                + [out["models"][m][arm]["weak_visual"]]
                for m in models
                for arm in arms
                if arm in out["models"][m]
            ],
        ),
        "",
    ]
    if stats:
        md += [
            f"This process: {stats.get('calls')} calls, tokens in/out {stats.get('prompt_tokens')}/{stats.get('completion_tokens')}, median call latency {stats.get('latency_p50_s')} s.",
            "",
        ]
    return out, "\n".join(md)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="hard", choices=["hard", "legacy", "all"])
    ap.add_argument(
        "--models",
        default="qwen3.6:35b-a3b,qwen3.5:4b",
        help="comma-separated; the process runs ONE model (the first) unless --summarize-only",
    )
    ap.add_argument("--seeds", default="20260902,20260903")
    ap.add_argument("--runs", type=int, default=1, help="repeat runs per seed; run k uses seed + 1000*(k-1)")
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--endpoint", default="http://100.127.85.58:11434/v1")
    ap.add_argument("--think", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--tasks", default="")
    ap.add_argument("--max-calls", type=int, default=1300, help="per process (i.e. per model)")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--summarize-only", action="store_true")
    ap.add_argument("--tag", default="v2")
    args = ap.parse_args(argv)
    models = [m for m in args.models.split(",") if m]
    cfg = {
        "set": args.set,
        "model": models[0],
        "seeds": [int(s) for s in args.seeds.split(",") if s],
        "runs": args.runs,
        "max_steps": args.max_steps,
        "temperature": args.temperature,
        "endpoint": args.endpoint,
        "think": args.think,
        "max_tokens": args.max_tokens if not args.think else max(args.max_tokens, 2000),
        "arms": args.arms.split(","),
        "tasks": [t for t in args.tasks.split(",") if t],
        "max_calls": args.max_calls,
        "resume": args.resume,
        "tag": args.tag,
    }
    if args.summarize_only:
        rows = load_rows(models)
    else:
        asyncio.run(run(cfg))
        rows = load_rows(models)
    rows = [r for r in rows if r.get("think", False) == cfg["think"]]
    summary, md = summarize(rows, cfg)
    info = env_info(
        {
            "chromium": cfg.get("chromium"),
            "models": models,
            "model_endpoint": cfg["endpoint"],
            "think": cfg["think"],
            "temperature": cfg["temperature"],
            "seeds": cfg["seeds"],
            "runs": cfg["runs"],
            "max_steps": cfg["max_steps"],
            "max_refusals": MAX_REFUSALS,
            "llm_stats": cfg.get("llm_stats"),
            "set": cfg["set"],
        }
    )
    md += "\n## Environment\n\n" + env_md(info) + "\n"
    jp, mp = write_results("agent_loop_v2", {"env": info, "summary": summary, "episodes": rows}, md)
    log(f"wrote {jp} and {mp}")


if __name__ == "__main__":
    main()
