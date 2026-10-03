"""The manager: one orchestrator + two narrow specialists (the claims squad).

Pattern: orchestrator-worker (a.k.a. manager-plus-specialists).

    plan            manager decomposes the claim question (1 model call)
    dispatch #1     -> coverage/exclusions worker   (narrow tools)
    dispatch #2     -> adjuster-note summary worker (narrow tools)
    synthesize      manager re-sends both findings and decides status
    assemble        code asserts the claim record fields + compute_payout

Every model call is written to the hand-off log with its token counts, so the
context re-send multiplier is measured, not guessed.

Failure policy (one retry, then degrade - never guess coverage):
    a specialist answering HTTP 500 is retried once; if it fails again the
    orchestrator degrades to a PARTIAL answer (identity facts, status=null,
    no coverage assertion).  It never declares a claim covered without the
    exclusions check - that would be the lie, and the failure-case report
    checks for exactly that.
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_WEEK7 = Path(__file__).resolve().parent.parent / "week7"
if str(_WEEK7) not in sys.path:
    sys.path.insert(0, str(_WEEK7))

import llm                                            # noqa: E402
import outputs                                        # noqa: E402
from agent import Budgets, parse_tool_json            # noqa: E402
from tools import call_tool                           # noqa: E402

from handoffs import HOP_COVERAGE, HOP_PLANNER, HOP_SUMMARY, HOP_SYNTHESIS, make_call  # noqa: E402
from race_config import (                             # noqa: E402
    CALL_PACE_S,
    MAX_COST,
    MAX_ITERS,
    MAX_SECONDS,
    MAX_TOKENS,
    WORKER_MAX_ATTEMPTS,
    WORKER_RETRY_BACKOFF_S,
)
from workers import (                                 # noqa: E402
    WorkerHTTPError,
    run_coverage_worker,
    run_summary_worker,
)

PLAN_SYSTEM = """You are the MANAGER of a three-agent insurance-claims squad
(you + a coverage/exclusions specialist + an adjuster-note summary specialist).

Given ONE claim question, write the decomposition you will hand to your two
specialists. You do NOT answer the claim yourself and you do NOT call tools.
The only fields you hold are the claim slice: claim_id, title, adjuster_notes -
list `inputs` from those (you never invent fields you were not given).

Return STRICT JSON only:
{"plan": [
  {"worker": "coverage_exclusions_worker", "scope": "<one line: what this worker must determine>",
   "inputs": ["<fields of the claim slice you will send it>"]},
  {"worker": "adjuster_note_summary_worker", "scope": "<one line>", "inputs": ["<fields>"]}
]}
The two scopes must be independent so the two workers can run in parallel.
"""

SYNTHESIS_SYSTEM = """You are the SYNTHESIS step of a three-agent insurance-claims squad.
You receive three JSON objects: claim_facts (from the manager's own get_claim call),
coverage_finding (from the coverage/exclusions specialist) and summary_finding (from the
adjuster-note summary specialist). Decide the final claim status and exclusion clause.

STATUS RULES (same decision procedure the single agent is given, so both arms
are told what the labels mean):
- "approved"  when the loss is covered and NO whole-loss exclusion applies.
- "excluded"  when coverage_finding.coverage_verdict == "excluded" OR
  coverage_finding.exclusion_clause is not null. Quote that exact clause id.
- "rejected"  when summary_finding.denial_language is true and no exclusion clause applies.
- A PARTIAL approval counts as "approved": when summary_finding.partial_approval is
  true (pay part, exclude another part, or approve subject to a condition) and the
  coverage finding names NO clause, the status is "approved" - the covered part pays.
- "unclear"   is a last resort: only when the coverage finding is "unclear" AND the
  summary shows neither a partial approval nor a clear denial.

HARD RULES - these are the failure modes this team is measured on:
- NEVER round "covered subject to exclusion Exc-X.Y" up to plain "covered"/approved.
  If the coverage finding names ANY clause id, the status is "excluded".
- NEVER claim coverage when the coverage finding carries no policy evidence
  (coverage_verdict "unclear"): use status "unclear".
- Never invent a clause id that is not in the coverage finding.
- Identity fields (claim_number, loss_date, excess, payout) are NOT yours: the
  manager asserts them from the claim record. You decide status + clause only.

Return STRICT JSON only:
{"status": "approved"|"excluded"|"rejected"|"unclear",
 "exclusion_clause": "Exc-X.Y"|null,
 "decision_note": "<one sentence>"}
"""


@dataclass
class OrchestratorRun:
    claim_id: str
    system: str = "orchestrator"
    iterations: int = 0                       # model calls across the whole team
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
    handoffs: list = field(default_factory=list)
    model: str = ""
    injected_failure: bool = False
    degraded: bool = False
    guardrail_fired: bool = False
    worker_attempts: dict = field(default_factory=dict)
    plan: dict = None

    # -- logging ----------------------------------------------------------
    def to_log_lines(self, expected=None):
        lines = [f"[CLAIM {self.claim_id}]", "[SYSTEM] ORCHESTRATOR (claims squad)"]
        lines.append(f"[PLAN] {json.dumps(self.plan, ensure_ascii=False)}")
        for i, rec in enumerate(self.handoffs, 1):
            lines.append(
                f"[HANDOFF {i}] hop={rec['hop']} call={rec['call']} "
                f"status={rec['status']} prompt_tokens={rec['prompt_tokens']} "
                f"completion_tokens={rec['completion_tokens']} "
                f"total_tokens={rec['total_tokens']} "
                f"cost_usd={rec['cost_usd']:.6f} "
                f"latency_ms={rec['latency_ms']:.1f} detail={rec['detail']}"
            )
        for i, step in enumerate(self.steps, 1):
            lines.append(
                f"[STEP {i}] {step.get('phase')} / {step.get('name')}: "
                f"{json.dumps(step, ensure_ascii=False)}"
            )
        lines.append("[RESULT]")
        status = ("NOT_VALIDATED" if self.pass_result is None
                  else ("PASS" if self.pass_result else "FAIL"))
        lines.append(f"Status: {status}")
        if expected is not None:
            lines.append("Expected output: " + json.dumps(expected, ensure_ascii=False))
        lines.append("Actual output: " + json.dumps(self.output, ensure_ascii=False))
        if self.failures:
            lines.append("Failures: " + json.dumps(self.failures, ensure_ascii=False))
        if self.degraded:
            lines.append("DEGRADED: true")
        if self.guardrail_fired:
            lines.append("SYNTHESIS GUARDRAIL FIRED: coverage exclusion was not rounded up")
        lines.append("[METRICS]")
        lines.append(
            "tokens=%d (prompt=%d completion=%d)\ncost=$%.6f\nlatency_ms=%.1f\n"
            "termination=%s\ntermination_detail=%s\nmodel_calls=%d\nmodel=%s"
            % (self.tokens, self.prompt_tokens, self.completion_tokens, self.cost,
               self.latency_ms, self.termination_reason, self.termination_detail,
               self.iterations, self.model)
        )
        lines.append("[WORKER ATTEMPTS] " + json.dumps(self.worker_attempts,
                                                        ensure_ascii=False))
        return lines

    def to_dict(self):
        return {
            "claim_id": self.claim_id, "system": self.system,
            "iterations": self.iterations,
            "termination_reason": self.termination_reason,
            "termination_detail": self.termination_detail,
            "tokens": self.tokens, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost": round(self.cost, 6), "latency_ms": round(self.latency_ms, 1),
            "output": self.output, "pass_result": self.pass_result,
            "failures": self.failures, "steps": self.steps,
            "handoffs": self.handoffs, "model": self.model,
            "injected_failure": self.injected_failure, "degraded": self.degraded,
            "guardrail_fired": self.guardrail_fired,
            "worker_attempts": self.worker_attempts, "plan": self.plan,
        }


def _record_call(run, case, hop, call_index, status, usage, cost, latency_ms, detail):
    rec = make_call(case, hop, call_index, status,
                    usage.get("prompt_tokens", 0) if usage else 0,
                    usage.get("completion_tokens", 0) if usage else 0,
                    cost, latency_ms, detail)
    run.handoffs.append(rec)
    run.iterations += 1
    if usage:
        run.prompt_tokens += usage.get("prompt_tokens", 0)
        run.completion_tokens += usage.get("completion_tokens", 0)
        run.tokens += usage.get("total_tokens", 0)
    run.cost += cost
    return rec


def _plan(run, case, record, budgets, start):
    """Manager decomposes the claim question (one model call)."""
    payload = {
        "task": f"Triage claim {case}.",
        "claim_id": record.get("claim_id"),
        "title": record.get("title"),
        "question": ("Decompose this claim question into two independent specialist "
                     "tasks and return the plan JSON."),
    }
    messages = [
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    t0 = time.perf_counter()
    try:
        content, usage = llm.chat(messages, temperature=0.0, max_tokens=350,
                                  wall_deadline=max(
                                      budgets.max_seconds - (time.perf_counter() - start), 1.0))
    except Exception as exc:  # noqa: BLE001
        run.termination_reason = "llm_error"
        run.termination_detail = f"planner call failed: {exc}"
        return None
    latency_ms = (time.perf_counter() - t0) * 1000.0
    cost = llm.token_cost(usage)
    _record_call(run, case, HOP_PLANNER, 1, 200, usage, cost, latency_ms,
                 "manager decomposes the claim question")
    parsed = parse_tool_json(content)
    plan = (parsed or {}).get("plan") if isinstance(parsed, dict) else None
    if not isinstance(plan, list):
        plan = [
            {"worker": "coverage_exclusions_worker",
             "scope": "determine coverage and any exclusion clause",
             "inputs": ["claim_id", "title", "adjuster_notes"]},
            {"worker": "adjuster_note_summary_worker",
             "scope": "extract key facts from the adjuster notes",
             "inputs": ["claim_id", "title", "adjuster_notes"]},
        ]
        run.steps.append({"phase": "plan", "name": "fallback_plan",
                          "detail": "planner did not return JSON; default decomposition used"})
    time.sleep(CALL_PACE_S)
    reason, detail = budgets.check(run.iterations, run.tokens, run.cost,
                                   time.perf_counter() - start)
    if reason:
        run.termination_reason, run.termination_detail = reason, detail
        return None
    return plan


def _dispatch(run, case, fn, hop, worker_key, budgets, start, inject_500=False):
    """Dispatch work to a specialist, with one retry on HTTP 500."""
    attempts = 0
    result = None
    while attempts < WORKER_MAX_ATTEMPTS:
        attempts += 1
        # A worker rides out rate limits for as long as the TEAM's wall-clock
        # budget allows - the same treatment the single agent's loop gets.
        remaining_s = max(budgets.max_seconds - (time.perf_counter() - start), 1.0)
        try:
            result = fn(inject_500=inject_500, wall_deadline=remaining_s)
        except WorkerHTTPError as exc:
            result = None
            rec = make_call(case, hop, attempts, f"HTTP{exc.status}", 0, 0, 0.0, 0.0,
                            f"{worker_key} returned HTTP {exc.status}; no model usage billed")
            run.handoffs.append(rec)
            run.steps.append({"phase": "dispatch", "name": worker_key,
                              "attempt": attempts, "status": f"HTTP {exc.status}",
                              "retry": attempts < WORKER_MAX_ATTEMPTS})
            if attempts < WORKER_MAX_ATTEMPTS:
                time.sleep(WORKER_RETRY_BACKOFF_S)
            continue
        run.handoffs.extend(result.calls)
        run.iterations += len(result.calls)
        run.prompt_tokens += result.prompt_tokens
        run.completion_tokens += result.completion_tokens
        run.tokens += result.tokens
        run.cost += result.cost
        run.steps.append({"phase": "dispatch", "name": worker_key,
                          "attempt": attempts, "status": result.status,
                          "error": result.error,
                          "tools": [s["tool"] for s in result.steps],
                          "finding": result.payload})
        if result.ok:
            break
        # a non-HTTP worker failure (bad JSON, blocked tool, budget) is not
        # retried: retrying a prompt-level failure with the same input would
        # just double the bill.  Only transport failures retry.
        break

    run.worker_attempts[worker_key] = attempts

    reason, detail = budgets.check(run.iterations, run.tokens, run.cost,
                                   time.perf_counter() - start)
    if reason:
        run.termination_reason, run.termination_detail = reason, detail
        return None, attempts
    if result is None or not result.ok:
        return None, attempts
    time.sleep(CALL_PACE_S)
    return result, attempts


def _last_dispatch_status(run, worker_key):
    """Status string of the most recent dispatch step for a worker."""
    for step in reversed(run.steps):
        if step.get("phase") == "dispatch" and step.get("name") == worker_key:
            return step.get("status")
    return "unknown"


def _synthesize(run, case, record, coverage, summary, budgets, start):
    """Manager re-sends both worker findings and decides status + clause."""
    claim_facts = {
        "claim_id": record.get("claim_id"),
        "claim_number": record.get("claim_number"),
        "loss_date": record.get("loss_date"),
        "excess": record.get("excess"),
        "claim_amount": record.get("claim_amount"),
    }
    payload = {
        "claim_facts": claim_facts,
        "coverage_finding": coverage.payload,
        "summary_finding": summary.payload,
    }
    messages = [
        {"role": "system", "content": SYNTHESIS_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    t0 = time.perf_counter()
    try:
        content, usage = llm.chat(messages, temperature=0.0, max_tokens=400,
                                  wall_deadline=max(
                                      budgets.max_seconds - (time.perf_counter() - start), 1.0))
    except Exception as exc:  # noqa: BLE001
        run.termination_reason = "llm_error"
        run.termination_detail = f"synthesis call failed: {exc}"
        return None
    latency_ms = (time.perf_counter() - t0) * 1000.0
    cost = llm.token_cost(usage)
    _record_call(run, case, HOP_SYNTHESIS, 1, 200, usage, cost, latency_ms,
                 "manager re-sends claim facts + both worker findings")

    parsed = parse_tool_json(content)
    if not isinstance(parsed, dict) or "status" not in parsed:
        run.termination_reason = "invalid_action"
        run.termination_detail = "synthesis did not return a status JSON"
        return None

    status = parsed.get("status")
    clause = parsed.get("exclusion_clause")
    clause = None if clause in (None, "null") else str(clause).strip() or None

    # Deterministic guardrail: a coverage finding that names an exclusion can
    # never be rounded up to approved by the synthesis prompt.  Logged when it
    # fires so the cost of the guard is visible.
    cov_clause = coverage.payload.get("exclusion_clause")
    cov_verdict = coverage.payload.get("coverage_verdict")
    if cov_clause and status != "excluded":
        run.guardrail_fired = True
        run.steps.append({"phase": "synthesis", "name": "guardrail_override",
                          "from_status": status, "to_status": "excluded",
                          "clause": cov_clause,
                          "reason": "coverage finding names an exclusion; "
                                    "synthesis may not round it up to covered"})
        status, clause = "excluded", cov_clause
    elif str(cov_verdict).lower() == "excluded" and status not in ("excluded", "unclear"):
        run.guardrail_fired = True
        run.steps.append({"phase": "synthesis", "name": "guardrail_override",
                          "from_status": status, "to_status": "excluded",
                          "clause": clause,
                          "reason": "coverage verdict is excluded; synthesis may not "
                                    "declare the claim covered"})
        status = "excluded"

    time.sleep(CALL_PACE_S)
    reason, detail = budgets.check(run.iterations, run.tokens, run.cost,
                                   time.perf_counter() - start)
    if reason:
        run.termination_reason, run.termination_detail = reason, detail
        return None
    run.steps.append({"phase": "synthesis", "name": "decision",
                      "status": status, "exclusion_clause": clause,
                      "decision_note": parsed.get("decision_note", ""),
                      "guardrail_fired": run.guardrail_fired})
    return {"status": status, "exclusion_clause": clause,
            "decision_note": parsed.get("decision_note", "")}


def _partial_output(run, record, reason):
    """Degrade path: identity facts only, NO coverage assertion."""
    run.degraded = True
    return {
        "claim_id": record.get("claim_id"),
        "claim_number": record.get("claim_number"),
        "loss_date": record.get("loss_date"),
        "status": None,
        "excess": record.get("excess"),
        "exclusion_clause": None,
        "payout": None,
        "degraded": True,
        "degraded_reason": reason,
    }


def run_orchestrator(claim_id, budgets=None, expected=None, inject_500=False):
    """Run the claims squad on one claim.  Returns an OrchestratorRun."""
    from claims import get_claim_record

    budgets = budgets or Budgets(MAX_ITERS, MAX_TOKENS, MAX_COST, MAX_SECONDS)
    record = get_claim_record(claim_id)
    run = OrchestratorRun(claim_id=claim_id, injected_failure=inject_500)
    run.model = llm.get_model()
    start = time.perf_counter()

    # Manager's own tool call: fetch the claim once (code, 0 tokens).
    run.steps.append({"phase": "manager", "name": "get_claim",
                      "status": "ok", "tokens": 0})

    run.plan = _plan(run, claim_id, record, budgets, start)
    if run.termination_reason == "llm_error":
        run.latency_ms = (time.perf_counter() - start) * 1000.0
        return run
    if run.termination_reason not in ("not_started", "completed"):
        run.latency_ms = (time.perf_counter() - start) * 1000.0
        return run

    claim_slice = {
        "claim_id": record.get("claim_id"),
        "title": record.get("title"),
        "adjuster_notes": record.get("adjuster_notes"),
    }

    # --- dispatch #1: coverage / exclusions specialist ---------------------
    coverage, cov_attempts = _dispatch(
        run, claim_id, lambda inject_500, wall_deadline=None: run_coverage_worker(
            claim_id, claim_slice, inject_500=inject_500,
            wall_deadline=wall_deadline),
        HOP_COVERAGE, "coverage_exclusions_worker", budgets, start,
        inject_500=inject_500,
    )

    # --- dispatch #2: adjuster-note summary specialist (independent) -------
    summary, sum_attempts = _dispatch(
        run, claim_id, lambda inject_500, wall_deadline=None: run_summary_worker(
            claim_id, claim_slice, inject_500=inject_500,
            wall_deadline=wall_deadline),
        HOP_SUMMARY, "adjuster_note_summary_worker", budgets, start,
        inject_500=False,
    )

    # --- failure policy: one retry, then degrade (never guess coverage) ----
    if coverage is None:
        status = _last_dispatch_status(run, "coverage_exclusions_worker")
        if str(status).startswith("HTTP"):
            cause = (f"coverage/exclusions worker returned {status.replace('HTTP', 'HTTP ')} "
                     f"on all {cov_attempts} attempt(s); no policy evidence available")
        else:
            cause = (f"coverage/exclusions worker produced no usable finding "
                     f"(status={status}) after {cov_attempts} attempt(s)")
        reason = (f"{cause}; no coverage decision was asserted - degraded to a "
                  "partial answer instead")
        run.termination_reason = "worker_failure_degraded"
        run.termination_detail = reason
        run.output = _partial_output(run, record, reason)
        run.latency_ms = (time.perf_counter() - start) * 1000.0
        if expected is not None:
            run.pass_result, run.failures = outputs.validate_output(run.output, expected)
        return run

    if summary is None and run.termination_reason in ("not_started", "completed"):
        run.termination_reason = "worker_failure"
        run.termination_detail = "adjuster-note summary worker produced no finding"
        run.output = _partial_output(
            run, record, "adjuster-note summary worker produced no finding")
        run.latency_ms = (time.perf_counter() - start) * 1000.0
        if expected is not None:
            run.pass_result, run.failures = outputs.validate_output(run.output, expected)
        return run
    if summary is None:
        run.latency_ms = (time.perf_counter() - start) * 1000.0
        return run

    # --- synthesis ---------------------------------------------------------
    decision = _synthesize(run, claim_id, record, coverage, summary, budgets, start)
    if decision is None:
        run.latency_ms = (time.perf_counter() - start) * 1000.0
        if expected is not None and run.output is None:
            run.pass_result, run.failures = False, [
                run.termination_detail or "no output produced"]
        return run

    # --- assemble: code asserts the record's facts, then compute_payout ----
    payout_result = call_tool("compute_payout", {
        "claim_amount": record["claim_amount"],
        "excess": record["excess"],
        "claim_status": decision["status"] if decision["status"] in
        ("approved", "excluded", "rejected") else "rejected",
    }) if decision["status"] in ("approved", "excluded", "rejected") else None
    run.steps.append({"phase": "assemble", "name": "compute_payout",
                      "status": "ok" if payout_result else "skipped",
                      "status_decided": decision["status"],
                      "note": ("'unclear' is not a payable status: no payout is "
                               "computed and the claim is escalated in the answer")})

    if payout_result is None:
        run.output = {
            "claim_id": record.get("claim_id"),
            "claim_number": record.get("claim_number"),
            "loss_date": record.get("loss_date"),
            "status": None,
            "excess": record.get("excess"),
            "exclusion_clause": decision.get("exclusion_clause"),
            "payout": None,
            "escalate": True,
            "escalate_reason": decision.get("decision_note", "coverage unclear"),
        }
        run.termination_reason = "needs_human_review"
        run.termination_detail = ("synthesis could not resolve coverage; claim "
                                  "returned for human review instead of being paid")
    else:
        run.output = {
            "claim_id": record.get("claim_id"),
            "claim_number": record.get("claim_number"),
            "loss_date": record.get("loss_date"),
            "status": decision["status"],
            "excess": record.get("excess"),
            "exclusion_clause": decision.get("exclusion_clause"),
            "payout": payout_result["payout"],
        }
        run.termination_reason = "completed"
        run.termination_detail = decision.get("decision_note", "")

    run.latency_ms = (time.perf_counter() - start) * 1000.0
    if expected is not None:
        if run.output is not None:
            run.pass_result, run.failures = outputs.validate_output(run.output, expected)
        else:
            run.pass_result, run.failures = False, ["no output produced"]
    return run
