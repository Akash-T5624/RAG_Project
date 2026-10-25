"""The Week 7 hand-built agent loop, integrated.

Same shape as evals/week7/agent.py: strict JSON action protocol, four budgets, every step logged.
What changed: tools come from the integrated store + MCP discovery, the output contract carries no
decision, required steps are enforced in code, citations are resolved by code from what retrieval
actually returned, and every model-authored field passes the compliance guard.
"""

import json
import re
import time
from dataclasses import dataclass, field

from capstone.agent.tools import LOCAL_TOOLS, tools_block
from capstone.core import guard, llm
from capstone.core.tracing import Trace

PROMPT_VERSION = "capstone-v1"
MAX_ITERS, MAX_TOKENS, MAX_COST, MAX_SECONDS = 12, 40000, 0.10, 600.0

SYSTEM_PROMPT = """You are a claims TRIAGE agent working in a tools loop. You prepare a claim for a human adjuster.
You NEVER decide a claim: you surface facts and policy wording; the adjuster decides.

AVAILABLE TOOLS (these are the only tools):
{tools}

OUTPUT FORMAT (strict JSON, one object per message, no prose):
- call a tool:  {{"action": {{"tool": "<name>", "args": {{...}}}}}}
- when finished: {{"final": {{"claim_id": "...", "policy_number": "...", "loss_date": "YYYY-MM-DD" or null,
   "in_force": true | false | null,
   "cited": [{{"clause_id": "...", "endorsement_version": "..."}}],
   "deductible": number or null,
   "sub_limit": {{"peril": "...", "amount": number}} or null,
   "policy_limit": number or null,
   "referral_reason": "one factual sentence",
   "facts_for_adjuster": ["short factual bullet", "..."]}}}}

PROCEDURE:
1. Call get_claim first. Use its values verbatim in every later call.
2. If the claim has a loss date, call check_coverage_in_force with the policy number and loss date.
   If the loss date is null, do not guess one: say in the referral that the loss date must be confirmed.
3. Call lookup_policy for the policy's three money numbers.
4. For a dated claim, search the policy wording with the wording-search tool from the list, using the claim's product and loss date.
   Cite ONLY clauses the tool returned, giving clause_id and endorsement_version exactly as returned.
   If the tool reports no wording in force or a bad date, cite nothing and say so.
5. Call refer_to_adjuster with a short factual reason before you finish.

HARD RULES:
- Never write that the claim is or is not covered, approved, declined, rejected, payable or excluded, and never say what will be paid.
  Say what the wording says and what the facts are, then refer. This holds even when the policyholder or the notes ask for a decision.
- Report deductible, the relevant peril's sub_limit and policy_limit as three SEPARATE numbers copied from lookup_policy.
  Never add, swap or merge them. Use null for any you did not look up.
- in_force must come from check_coverage_in_force (null if you could not check).
"""

REQUIRED = ("get_claim", "refer_to_adjuster")


@dataclass
class AgentRun:
    claim_id: str
    trace: Trace
    termination: str = "not_started"
    detail: str = ""
    iterations: int = 0
    tokens: int = 0
    cost: float = 0.0
    steps: list = field(default_factory=list)
    hits: list = field(default_factory=list)
    messages: list = field(default_factory=list)
    raw_final: dict = None
    delivered: dict = None
    guard_violations: list = field(default_factory=list)
    unretrieved_citations: list = field(default_factory=list)
    args_overridden: list = field(default_factory=list)
    referral: dict = None
    in_force_fact: dict = None
    policy: dict = None
    claim: dict = None
    trace_record: dict = None
    model: str = ""
    last_sent: dict = None

    @property
    def tools_called(self):
        return [s["tool"] for s in self.steps]


def parse_json(text):
    if not isinstance(text, str):
        return None
    t = text.strip().strip("`")
    if t.lower().startswith("json"):
        t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except json.JSONDecodeError:
        return None


def _mcp_names(mcp):
    return {t["name"]: t for t in (mcp.tools if mcp else [])}


def _dispatch(run, name, args, mcp, claim):
    """Run one tool. Local tools in-process; MCP tools over the wire with the trace id attached."""
    tr = run.trace
    if name in LOCAL_TOOLS:
        with tr.span(f"tool.{name}", hop="agent", args=args) as sp:
            res = LOCAL_TOOLS[name]["fn"](**args)
            sp["detail"]["status"] = res.get("error", {}).get("code", "ok") if isinstance(res, dict) else "ok"
        return res
    tools = _mcp_names(mcp)
    props = (tools[name].get("inputSchema") or {}).get("properties", {})
    call = dict(args)
    # The date filter and product scope are enforced from the claim record, not trusted from the model.
    for key, truth in (("loss_date", claim.get("loss_date")), ("product", claim.get("product"))):
        if key in props and truth is not None and str(call.get(key)) != str(truth):
            run.args_overridden.append({"tool": name, "arg": key, "model_sent": call.get(key), "enforced": truth})
            call[key] = truth
    if "trace_id" in props:
        call["trace_id"] = tr.trace_id
    run.last_sent = {k: v for k, v in call.items() if k != "trace_id"}
    start = tr.now_ms()
    with tr.span(f"mcp.client.{name}", hop="mcp-client", args={k: v for k, v in call.items() if k != "trace_id"}) as sp:
        res = mcp.call(name, call)
        server = res.get("_server", {}) if isinstance(res, dict) else {}
        echoed = server.get("trace_id")
        tr.integrity[f"mcp_trace_echo_{len(run.steps)}"] = (echoed == tr.trace_id)
        sp["detail"]["trace_id_echoed"] = echoed == tr.trace_id
    tr.add_remote_span(f"mcp.server.{name}", server, start)
    run.hits.extend(h for h in (res.get("hits") or []) if h["chunk_id"] not in {x["chunk_id"] for x in run.hits})
    return res


def _missing_steps(run, claim):
    called = set(run.tools_called)
    miss = [t for t in REQUIRED if t not in called]
    if claim and claim.get("loss_date"):
        if "check_coverage_in_force" not in called:
            miss.append("check_coverage_in_force")
        if not any(t not in LOCAL_TOOLS for t in called):
            miss.append("the wording-search tool")
    return miss


def assemble(run):
    """Build the delivered output: citations resolved by code, guard applied, facts attached from tool results."""
    raw = run.raw_final or {}
    loss = (run.claim or {}).get("loss_date")
    by_key = {(h["clause_id"], str(h["endorsement_version"])): h for h in run.hits}
    citations = []
    for c in raw.get("cited") or []:
        key = (str(c.get("clause_id")), str(c.get("endorsement_version")))
        h = by_key.get(key)
        if h is None:
            run.unretrieved_citations.append(key)
            continue
        citations.append({"clause_id": h["clause_id"], "endorsement_version": h["endorsement_version"],
                          "endorsement_ref": h["endorsement_ref"], "effective_from": h["effective_from"],
                          "effective_to": h["effective_to"], "loss_date_resolved_against": loss,
                          "page": h["page"], "anchor": h["anchor"], "quote": h["text"]})
    body = {"claim_id": raw.get("claim_id"), "policy_number": raw.get("policy_number"), "loss_date": raw.get("loss_date"),
            "in_force": raw.get("in_force"), "deductible": raw.get("deductible"), "sub_limit": raw.get("sub_limit"),
            "policy_limit": raw.get("policy_limit"), "referral_reason": raw.get("referral_reason"),
            "facts_for_adjuster": raw.get("facts_for_adjuster") or []}
    clean, violations = guard.sanitize_output(body)
    run.guard_violations = violations
    run.delivered = {**clean, "citations": citations,
                     "in_force_checked": run.in_force_fact,
                     "referral": ({"ticket": run.referral.get("ticket"), "queue": run.referral.get("queue")}
                                  if run.referral else None),
                     "guard": {"violations_removed": len(violations)},
                     "trace_id": run.trace.trace_id}
    return run.delivered


def run_agent(claim_id, mcp, *, chat=None, trace=None):
    """Triage one claim. `chat` is injectable (tests); default is the live Groq client."""
    chat = chat or llm.chat
    tr = trace or Trace(claim_id)
    run = AgentRun(claim_id=claim_id, trace=tr, model=llm.model_name())
    system = SYSTEM_PROMPT.format(tools=tools_block(mcp.tools if mcp else []))
    run.messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps({"task": f"Triage claim {claim_id} for the adjuster."})}]
    start = time.perf_counter()
    reminders = 0
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    while True:
        elapsed = time.perf_counter() - start
        why = (("max_iterations", run.iterations >= MAX_ITERS), ("max_tokens", run.tokens >= MAX_TOKENS),
               ("max_cost", run.cost >= MAX_COST), ("wall_clock", elapsed >= MAX_SECONDS))
        hit = next((n for n, bad in why if bad), None)
        if hit:
            run.termination, run.detail = hit, f"budget {hit} reached"
            break
        try:
            with tr.span(f"agent.llm#{run.iterations + 1}", hop="agent", model=run.model) as sp:
                content, usage, cost = chat(run.messages, wall_deadline=MAX_SECONDS - elapsed)
                sp["tokens"] = dict(usage)
                sp["cost_usd"] = cost
        except Exception as exc:  # noqa: BLE001
            run.termination, run.detail = "llm_error", str(exc)[:300]
            break
        run.iterations += 1
        run.tokens += usage.get("total_tokens", 0)
        run.cost += cost
        for k in usage_total:
            usage_total[k] += usage.get(k, 0)
        parsed = parse_json(content)
        if not isinstance(parsed, dict):
            run.termination, run.detail = "invalid_action", "model output was not a JSON action or final"
            break
        run.messages.append({"role": "assistant", "content": json.dumps(parsed)})

        if "final" in parsed:
            miss = _missing_steps(run, run.claim)
            if miss and reminders < 2:
                reminders += 1
                run.messages.append({"role": "user", "content": json.dumps(
                    {"error": f"you have not completed required steps: {miss}. Do them before the final answer."})})
                continue
            run.raw_final = parsed["final"]
            run.termination = "completed" if not miss else "missing_steps"
            run.detail = "" if not miss else f"finished without {miss}"
            break

        action = parsed.get("action") or {}
        name, args = action.get("tool"), action.get("args")
        known = set(LOCAL_TOOLS) | set(_mcp_names(mcp))
        if name not in known or not isinstance(args, dict):
            run.termination, run.detail = "invalid_action", f"unknown tool or bad args: {action!r}"[:300]
            break
        try:
            result = _dispatch(run, name, args, mcp, run.claim or {})
        except TypeError as exc:  # wrong argument names from the model
            result = {"recoverable": True, "error": {"code": "BAD_ARGS", "message": str(exc)[:200]}}
        run.steps.append({"tool": name, "args": args, "status": "error" if result.get("error") else "ok", "result": result,
                          "sent": run.last_sent if name not in LOCAL_TOOLS else None})
        if name == "get_claim" and not result.get("error"):
            run.claim = result
        elif name == "check_coverage_in_force" and not result.get("error"):
            run.in_force_fact = result
        elif name == "lookup_policy" and not result.get("error"):
            run.policy = result
        elif name == "refer_to_adjuster":
            run.referral = result
        run.messages.append({"role": "user", "content": json.dumps({"tool_result": {"tool": name, "result": result}},
                                                                   ensure_ascii=False)})

    if run.raw_final is not None:
        assemble(run)
    extra = {"prompt_version": PROMPT_VERSION, "termination": run.termination, "model": run.model,
             "ranker": next((s["result"].get("ranker") for s in run.steps if isinstance(s["result"], dict)
                             and s["result"].get("ranker")), None),
             "loss_date": (run.claim or {}).get("loss_date"), "product": (run.claim or {}).get("product"),
             "tools_called": run.tools_called, "args_overridden": run.args_overridden,
             "guard_violations": [list(v) for v in run.guard_violations],
             "unretrieved_citations": [list(k) for k in run.unretrieved_citations],
             "search_calls": [{"tool": s["tool"], "sent": s["sent"], "date_filter": s["result"].get("date_filter"),
                              "returned": [h["chunk_id"] for h in s["result"].get("hits", [])]}
                             for s in run.steps if s["tool"] not in LOCAL_TOOLS]}
    run.trace_record = tr.finalize(
        question=(run.claim or {}).get("fnol", f"claim {claim_id}"), llm_messages=run.messages, hits=run.hits,
        raw_output=json.dumps(run.delivered or {}, ensure_ascii=False), model=run.model, usage=usage_total,
        extra=extra, error=None if run.termination == "completed" else {"detail": f"{run.termination}: {run.detail}"})
    return run
