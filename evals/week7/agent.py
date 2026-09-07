import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import llm
import outputs
from config import AGENT_CALL_PACE_S, LOG_DIR
from tools import TOOLS, call_tool, tool_descriptions_block

FINAL_CONTRACT = {
    "claim_id": "string",
    "claim_number": "string",
    "loss_date": 'string "YYYY-MM-DD" or null',
    "status": '"approved" | "excluded" | "rejected"',
    "excess": "number or null",
    "exclusion_clause": 'string "Exc-X.Y" or null',
    "payout": "number (INR)",
}

SYSTEM_PROMPT = f"""You are an insurance claims triage agent working inside a tools loop.

You process ONE claim. You decide which tool to call next based on the result of
the previous tool. Each message from the user is either the initial task or the
result of a tool you called. NEVER explain your reasoning. NEVER write prose.

AVAILABLE TOOLS:
{tool_descriptions_block()}

OUTPUT FORMAT (strict JSON, one object per message):
- To call a tool, output exactly:
  {{"action": {{"tool": "<tool_name>", "args": {{<argument names and values>}}}}}}
- When the claim is fully processed, output exactly:
  {{"final": {{"claim_id": "...", "claim_number": "...", "loss_date": <date or null>,
   "status": "...", "excess": <number or null>, "exclusion_clause": <"Exc-X.Y" or null>,
   "payout": <number>}}}}

HARD RULES:
- You MUST call get_claim before any final answer. Then every call to a tool
  that needs the record (search_policy, compute_payout, final) must use the
  values verbatim from the get_claim result. NEVER answer from memory: the claim
  number, loss date, excess and claim amount are only known from the tool
  result, so a final answer produced without calling get_claim is an error.
- If the notes raise a coverage or exclusion point (water ingress, tyres, theft
  of accessories, unlicensed or intoxicated driver, commercial use, racing,
  aftermarket accessories, ...), you MUST call search_policy with a targeted
  query before deciding the status, and you MUST quote the exact exclusion
  clause id (e.g. "Exc-7.4") the policy passage actually names in the final
  answer.
- You MUST call compute_payout with the claim amount and excess from the
  get_claim result and the status you decided, and set the final payout to the
  returned value.
- Do not produce the final object until you have all the values.

DECISION PROCEDURE:
1. Call get_claim(claim_id=<id from the task>) first.
2. Read the adjuster notes. If they raise a coverage or exclusion point, call
   search_policy with a targeted query and read the passage the tool returns.
3. Decide the status using the notes AND the policy passage, if any:
   - "approved"  when the loss is covered;
   - "excluded"  when a policy exclusion clause applies;
   - "rejected"  when the claim is denied for a reason that is not a policy
     exclusion.  A partial approval counts as "approved".
4. Call compute_payout with the numeric claim amount and excess from the claim
   record and the status you decided.
5. Produce the final JSON: copy claim_number, loss_date, excess verbatim from
   the get_claim result (null for loss_date when the notes do not give a date),
   set exclusion_clause to the clause id the policy result named (e.g.
   "Exc-7.4"), and payout equal to the compute_payout result.
"""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_tool_json(text):
    """Parse a strict-JSON action/final message robustly."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


@dataclass
class Budgets:
    max_iters: int
    max_tokens: int
    max_cost: float
    max_seconds: float

    @classmethod
    def from_config(cls):
        import config

        return cls(
            max_iters=config.MAX_ITERS,
            max_tokens=config.MAX_TOKENS,
            max_cost=config.MAX_COST,
            max_seconds=config.MAX_SECONDS,
        )

    def check(self, iterations, tokens, cost, elapsed_s):
        """Return (termination_reason, detail) or (None, None) if within budget."""
        if iterations >= self.max_iters:
            return ("max_iterations",
                    f"current iterations: {iterations}, max iterations: {self.max_iters}")
        if tokens >= self.max_tokens:
            return ("max_tokens",
                    f"current tokens: {tokens}, max tokens: {self.max_tokens}")
        if cost >= self.max_cost:
            return ("max_cost",
                    f"current cost: ${cost:.6f}, max cost: ${self.max_cost:.6f}")
        if elapsed_s >= self.max_seconds:
            return ("wall_clock",
                    f"current elapsed: {elapsed_s:.1f}s, max seconds: {self.max_seconds:.1f}")
        return (None, None)

    def check_post_call(self, tokens, cost, elapsed_s):
        """Post-call check: tokens/cost/wall-clock only (NOT iterations).

        This runs right after an LLM call. A single call can cross the token,
        cost or time budget; when that happens we stop before executing any
        further tool call.  The iteration budget is NOT checked here so a final
        answer produced exactly on the max-th call still completes normally.
        """
        if tokens >= self.max_tokens:
            return ("max_tokens",
                    f"current tokens: {tokens}, max tokens: {self.max_tokens}")
        if cost >= self.max_cost:
            return ("max_cost",
                    f"current cost: ${cost:.6f}, max cost: ${self.max_cost:.6f}")
        if elapsed_s >= self.max_seconds:
            return ("wall_clock",
                    f"current elapsed: {elapsed_s:.1f}s, max seconds: {self.max_seconds:.1f}")
        return (None, None)


@dataclass
class AgentRun:
    claim_id: str
    system: str = "agent"
    iterations: int = 0
    termination_reason: str = "not_started"
    termination_detail: str = ""
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    output: dict = None
    pass_result: bool = None
    failures: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    model: str = ""

    def to_log_lines(self, expected=None):
        lines = [f"[CLAIM {self.claim_id}]", f"[SYSTEM] {self.system.upper()}"]
        for i, step in enumerate(self.steps, 1):
            lines.append(f"[ITERATION {i}]")
            lines.append(
                f"Tool: {step.get('tool')}\n"
                f"Args: {json.dumps(step.get('args', {}), ensure_ascii=False)}\n"
                f"Tool result/status: {step.get('status')}\n"
                f"Timestamp: {step.get('timestamp')}\n"
                f"tokens this call: {step.get('tokens_this_call')}\n"
                f"cumulative tokens: {step.get('cumulative_tokens')}\n"
                f"cumulative cost: ${step.get('cumulative_cost', 0.0):.6f}\n"
                f"elapsed: {step.get('elapsed_s', 0.0):.3f}s"
            )
        lines.append("[RESULT]")
        if self.pass_result is not None:
            status = "PASS" if self.pass_result else "FAIL"
        else:
            status = "NOT_VALIDATED"
        lines.append(f"Status: {status}")
        if expected is not None:
            lines.append(f"Expected output: {json.dumps(expected, ensure_ascii=False)}")
            lines.append(f"Actual output: {json.dumps(self.output, ensure_ascii=False) if self.output else None}")
        if self.failures:
            lines.append(f"Failures: {json.dumps(self.failures, ensure_ascii=False)}")
        lines.append("[METRICS]")
        lines.append(
            "tokens=%d\ncost=$%.6f\nlatency_ms=%.1f\ntermination=%s\niterations=%d\n"
            "termination_detail=%s"
            % (self.tokens, self.cost, self.latency_ms, self.termination_reason,
               self.iterations, self.termination_detail)
        )
        if self.termination_reason != "not_started" and self.termination_reason not in (
            "completed", "tool_failure", "invalid_action", "llm_error"
        ):
            lines.append("[BUDGET]")
            lines.append(
                f"Budget triggered: {self.termination_reason.upper()}\n"
                f"{self.termination_detail}\nAgent terminated cleanly."
            )
        return lines


def write_log(run: AgentRun, claim_id, expected=None, system="agent"):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{system}_{claim_id}.log"
    body = "\n".join(run.to_log_lines(expected)) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def run_agent(claim_id, budgets=None, expected=None, temperature=0.0):
    """Run the ReAct agent on one claim. Returns an AgentRun record."""
    budgets = budgets or Budgets.from_config()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(
            {"task": f"Triage claim {claim_id}.",
             "instructions": (
                 "Retrieve the claim, consult the policy where the notes require "
                 "it, decide the status, compute the payout, then produce the "
                 "final JSON.")
             })},
    ]

    run = AgentRun(claim_id=claim_id)
    run.model = llm.get_model()
    start = time.perf_counter()

    while True:
        elapsed_s = time.perf_counter() - start
        reason, detail = budgets.check(
            run.iterations, run.tokens, run.cost, elapsed_s
        )
        if reason:
            run.termination_reason = reason
            run.termination_detail = detail
            break

        try:
            remaining = budgets.max_seconds - (time.perf_counter() - start)
            content, usage = llm.chat(
                messages, temperature=temperature, wall_deadline=remaining
            )
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
            run.termination_detail = "LLM output could not be parsed as JSON action/final"
            break

        if "final" in parsed:
            if not any(s["tool"] == "get_claim" for s in run.steps):
                run.termination_reason = "invalid_action"
                run.termination_detail = (
                    "final answer produced without calling get_claim first "
                    "(claim data must come from the tool, not from memory)")
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

        # Pace between decision calls so the shared rate-limited backend can
        # refill its token window (throttling is part of the measured latency).
        time.sleep(AGENT_CALL_PACE_S)

    run.latency_ms = (time.perf_counter() - start) * 1000.0

    if expected is not None:
        if run.output is not None:
            run.pass_result, run.failures = outputs.validate_output(run.output, expected)
        else:
            run.pass_result, run.failures = False, [
                "no final output produced (termination: " + run.termination_reason + ")"
            ]

    write_log(run, claim_id, expected=expected)
    return run


def main(argv=None):
    import argparse
    import config

    parser = argparse.ArgumentParser(description="Run the hand-built claims agent")
    parser.add_argument("claim_id", default=config.RACE_CLAIM_IDS[0], nargs="?")
    parser.add_argument("--max-iters", type=int, default=config.MAX_ITERS)
    parser.add_argument("--max-tokens", type=int, default=config.MAX_TOKENS)
    parser.add_argument("--max-cost", type=float, default=config.MAX_COST)
    parser.add_argument("--max-seconds", type=float, default=config.MAX_SECONDS)
    args = parser.parse_args(argv)

    budgets = Budgets(args.max_iters, args.max_tokens, args.max_cost, args.max_seconds)
    from claims import get_claim_record
    import outputs as outs

    record = get_claim_record(args.claim_id)
    expected = outs.expected_output(record)
    run = run_agent(args.claim_id, budgets=budgets, expected=expected)
    print("\n".join(run.to_log_lines(expected)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())