"""evals/week8/agent.py — the claims agent *as it runs in production*.

This is the week-7 claims agent (same tools, same loop, same contracts) with
ONE documented change: the adjuster notes are treated as the primary,
authoritative evidence, and the policy lookup is an optional extra.  The
week-7 race prompt *compelled* `search_policy` on every exclusion claim, which
hides the exact failure the claims director saw: a correct payout computed
without ever opening the exclusions.  This "notes-first" variant is the honest
subject of the week-8 trajectory eval.  Everything else (budgets, reparses,
pacing, output contract, tool registry) is inherited from week 7 so the eval
measures trajectory, not agent re-engineering.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_WEEK7 = str(Path(__file__).resolve().parent.parent / "week7")
if _WEEK7 not in sys.path:
    sys.path.insert(0, _WEEK7)

import llm                       # noqa: E402  (week-7 llm stack)
import outputs                   # noqa: E402  (week-7 output contract)
from agent import Budgets, AgentRun, write_log, parse_tool_json  # noqa: E402
from config import AGENT_CALL_PACE_S  # noqa: E402
from tools import TOOLS, call_tool, tool_descriptions_block  # noqa: E402

os.environ.setdefault("WEEK7_MODEL", os.getenv("WEEK8_MODEL") or "qwen/qwen3.8-27b")

WEEK8_SYSTEM_PROMPT = f"""You are an insurance claims triage agent working inside a tools loop.

You process ONE claim. Each message from the user is either the initial task or
a tool result. NEVER write prose.

AVAILABLE TOOLS:
{tool_descriptions_block()}

OUTPUT FORMAT (strict JSON, one object per message):
- To call a tool: {{"action": {{"tool": "<tool_name>", "args": {{...}}}}}}
- When the claim is fully processed:
  {{"final": {{"claim_id": "...", "claim_number": "...", "loss_date": <date or null>,
   "status": "...", "excess": <number or null>, "exclusion_clause": <"Exc-X.Y" or null>,
   "payout": <number>}}}}

RULES:
- You MUST call get_claim first.  Copy claim_number, loss_date, excess and the
  claim amount VERBATIM from its result; never answer from memory.
- The adjuster notes are the primary evidence and their conclusion is
  authoritative.  You MAY call search_policy for extra detail, but the notes'
  verdict alone settles the case.  When the notes name an exclusion clause,
  use that clause id.
- You MUST call compute_payout with the claim amount, the excess and the status
  you decided; set the final payout to the returned value.

DECISION PROCEDURE:
1. Call get_claim(claim_id=<id from the task>).
2. Read the adjuster notes; optionally call search_policy for detail.
3. Decide the status from the notes (+ any policy passage you fetched):
   - "approved"  when the loss is covered;
   - "excluded"  when an exclusion clause applies (note its id);
   - "rejected"  when denied for a reason that is not a policy exclusion.
4. Call compute_payout with the numeric claim amount and excess from the claim
   record and the status you decided.
5. Produce the final JSON copying claim_number, loss_date, excess verbatim from
   the get_claim result and setting payout to the compute_payout result.
"""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_agent(claim_id, budgets=None, expected=None, temperature=0.0):
    """Run the production-form claims agent on one claim. Returns AgentRun."""
    budgets = budgets or Budgets.from_config()
    messages = [
        {"role": "system", "content": WEEK8_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(
            {"task": f"Triage claim {claim_id}.",
             "instructions": (
                 "Retrieve the claim, decide the status, compute the payout, "
                 "then produce the final JSON.")})},
    ]

    run = AgentRun(claim_id=claim_id)
    run.model = llm.get_model()
    start = time.perf_counter()

    while True:
        elapsed_s = time.perf_counter() - start
        reason, detail = budgets.check(run.iterations, run.tokens, run.cost, elapsed_s)
        if reason:
            run.termination_reason = reason
            run.termination_detail = detail
            break

        try:
            remaining = budgets.max_seconds - (time.perf_counter() - start)
            content, usage = llm.chat(messages, temperature=temperature,
                                      wall_deadline=remaining)
        except llm.WallBudgetExceeded as exc:
            run.termination_reason = "wall_clock"
            run.termination_detail = str(exc)
            break
        except Exception as exc:
            run.termination_reason = "llm_error"
            run.termination_detail = str(exc)
            break

        run.iterations += 1
        run.tokens += usage["total_tokens"]
        run.prompt_tokens += usage["prompt_tokens"]
        run.completion_tokens += usage["completion_tokens"]
        run.cost += llm.token_cost(usage)
        elapsed_s = time.perf_counter() - start

        reason, detail = budgets.check_post_call(run.tokens, run.cost, elapsed_s)
        if reason:
            run.termination_reason = reason
            run.termination_detail = detail
            break

        parsed = parse_tool_json(content)
        if parsed is None or not isinstance(parsed, dict):
            run.termination_reason = "invalid_action"
            run.termination_detail = "LLM output could not be parsed as JSON"
            break

        if "final" in parsed:
            if not any(s["tool"] == "get_claim" for s in run.steps):
                run.termination_reason = "invalid_action"
                run.termination_detail = "final without calling get_claim"
                break
            run.output = parsed.get("final")
            run.termination_reason = "completed"
            run.termination_detail = "agent produced a final structured output"
            break

        action = parsed.get("action")
        if not isinstance(action, dict):
            run.termination_reason = "invalid_action"
            run.termination_detail = "action object missing from LLM output"
            break

        tool_name = action.get("tool") or action.get("tool_name")
        args = action.get("args") or action.get("arguments") or action.get("parameters")
        if tool_name not in TOOLS or not isinstance(args, dict):
            run.termination_reason = "invalid_action"
            run.termination_detail = f"unknown tool or bad args: {action!r}"
            break

        step = {
            "tool": tool_name,
            "args": args,
            "timestamp": _now_iso(),
            "tokens_this_call": usage["total_tokens"],
            "cumulative_tokens": run.tokens,
            "cumulative_cost": run.cost,
            "elapsed_s": elapsed_s,
        }
        try:
            result = call_tool(tool_name, args)
            step["status"] = "ok"
            step["result"] = result
        except Exception as exc:
            result = {"error": str(exc)}
            step["status"] = "error"
            step["result"] = result
        run.steps.append(step)

        messages.append({"role": "assistant", "content": json.dumps(parsed)})
        messages.append({
            "role": "user",
            "content": json.dumps(
                {"tool_result": {"tool": tool_name, "status": step["status"],
                                 "result": result}},
                ensure_ascii=False,
            ),
        })
        time.sleep(AGENT_CALL_PACE_S)

    run.latency_ms = (time.perf_counter() - start) * 1000.0

    if expected is not None:
        if run.output is not None:
            run.pass_result, run.failures = outputs.validate_output(run.output, expected)
        else:
            run.pass_result, run.failures = False, [
                "no final output produced (termination: " + run.termination_reason + ")"
            ]

    write_log(run, claim_id, expected=expected, system="agent_w8")
    return run