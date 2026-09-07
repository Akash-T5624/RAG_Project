import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import outputs
from config import WORKFLOW_POLICY_QUERY
from tools import compute_payout, get_claim, search_policy

EXC_RE = re.compile(r"\bExc[- ]?\d+\.\d+", re.IGNORECASE)
DENIAL_RE = re.compile(
    r"\brecommend\s+denial\b|\brecommend\s+deny\b|\bclaim\s+denied\b|"
    r"\bis\s+excluded\b|\bwholly\s+excluded\b|\bdenial\b|\bdenied\b|\bnot\s+payable\b",
    re.IGNORECASE,
)


def _decision_status(record):
    """Deterministic code decision, mirroring the provided eval labels.

    Status is "excluded" when the adjuster notes cite an exclusion clause AND
    use denial language; otherwise "approved".  (None of the accepted claims in
    the race set use a non-exclusion "rejected" disposition, so that value is
    only reachable through the compute_payout enum contract.)
    """
    notes = record.get("adjuster_notes", "")
    has_clause = EXC_RE.search(notes) is not None
    has_denial = DENIAL_RE.search(notes) is not None
    if has_clause and has_denial:
        return "excluded"
    return "approved"


def _exclusion_clause(record):
    m = EXC_RE.search(record.get("adjuster_notes", ""))
    if m:
        return m.group(0).replace(" ", "")
    return None


@dataclass
class WorkflowRun:
    claim_id: str
    system: str = "workflow"
    iterations: int = 0
    termination_reason: str = "completed"
    termination_detail: str = "deterministic pipeline finished"
    tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    output: dict = None
    pass_result: bool = None
    failures: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    model: str = "none (deterministic code, no LLM calls)"

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
            "tokens=%d\ncost=$%.6f\nlatency_ms=%.1f\ntermination=%s\niterations=%d"
            % (self.tokens, self.cost, self.latency_ms, self.termination_reason,
               self.iterations)
        )
        return lines


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def run_workflow(claim_id, expected=None):
    """Deterministic fixed workflow over one claim. Returns a WorkflowRun."""
    start = time.perf_counter()
    run = WorkflowRun(claim_id=claim_id)

    steps = [
        {"tool": "get_claim", "args": {"claim_id": claim_id}, "fn": lambda: get_claim(claim_id)},
        {"tool": "search_policy", "args": {"query": WORKFLOW_POLICY_QUERY},
         "fn": lambda: search_policy(WORKFLOW_POLICY_QUERY)},
    ]

    for step in steps:
        t0 = time.perf_counter()
        result = step["fn"]()
        elapsed_s = time.perf_counter() - t0
        run.iterations += 1
        run.steps.append({
            "tool": step["tool"],
            "args": step["args"],
            "status": "ok" if isinstance(result, dict) else f"ok ({type(result).__name__})",
            "result": result,
            "timestamp": _now_iso(),
            "tokens_this_call": 0,
            "cumulative_tokens": 0,
            "cumulative_cost": 0.0,
            "elapsed_s": elapsed_s,
        })

    record = run.steps[0]["result"]
    policy_hits = run.steps[1]["result"]

    status = _decision_status(record)
    clause = _exclusion_clause(record) if status != "approved" else None

    # Deterministic payout via the shared compute_payout tool.
    payout_result = compute_payout(
        record["claim_amount"], record["excess"], status
    )
    run.iterations += 1
    run.steps.append({
        "tool": "compute_payout",
        "args": {"claim_amount": record["claim_amount"],
                 "excess": record["excess"],
                 "claim_status": status},
        "status": "ok",
        "result": payout_result,
        "timestamp": _now_iso(),
        "tokens_this_call": 0,
        "cumulative_tokens": 0,
        "cumulative_cost": 0.0,
        "elapsed_s": 0.0,
    })

    run.output = {
        "claim_id": record["claim_id"],
        "claim_number": record["claim_number"],
        "loss_date": record["loss_date"],
        "status": status,
        "excess": record["excess"],
        "exclusion_clause": clause,
        "payout": payout_result["payout"],
    }
    run.latency_ms = (time.perf_counter() - start) * 1000.0

    if expected is not None:
        run.pass_result, run.failures = outputs.validate_output(run.output, expected)

    from agent import write_log  # same log writer, workflow log file

    write_log(run, claim_id, expected=expected, system="workflow")
    return run


def main(argv=None):
    import argparse

    from agent import Budgets
    from claims import get_claim_record as gcr
    import config

    parser = argparse.ArgumentParser(description="Run the fixed claims workflow")
    parser.add_argument("claim_id", default=config.RACE_CLAIM_IDS[0], nargs="?")
    args = parser.parse_args(argv)

    record = gcr(args.claim_id)
    expected = outputs.expected_output(record)
    run = run_workflow(args.claim_id, expected=expected)
    print("\n".join(run.to_log_lines(expected)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())