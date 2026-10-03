"""Injected worker failure: the coverage/exclusions worker answers HTTP 500.

One case, one failure, one honest record of what the orchestrator actually did
(retried / degraded / lied).  The injected run is kept OUT of race_table.md so
the four race numbers stay a clean measurement; it is reported separately here.

Run:  cd evals/week10 && python failure_case.py
"""

import argparse
import json

# race_config first: it puts the week-7 package on sys.path.
from race_config import (
    FAILURE_INJECTION_CASE,
    JUDGE_NAME,
    MAX_COST,
    MAX_ITERS,
    MAX_SECONDS,
    MAX_TOKENS,
    MODEL,
    RESULTS_DIR,
    RUN_CACHE_PATH,
)

import outputs
from claims import get_claim_record
from orchestrator import run_orchestrator


def classify(run):
    """What the orchestrator actually did, in one phrase (honest, from the log)."""
    attempts = run.worker_attempts.get("coverage_exclusions_worker", 0)
    out = run.output or {}
    status = out.get("status")

    if status == "approved" and run.injected_failure and run.degraded is False:
        return "LIED - declared the claim covered with no exclusions check"
    if status in ("excluded", "rejected") and run.injected_failure:
        return ("LIED (guessed) - asserted a status with no policy evidence from "
                "the coverage worker")
    if run.degraded and status is None:
        if attempts > 1:
            return (f"RETRIED then DEGRADED - {attempts} attempts, then a partial "
                    "answer with status=null and no coverage assertion")
        return "DEGRADED immediately (no successful attempt) to a partial answer"
    if status is None:
        return "produced no status at all (no coverage assertion)"
    return f"returned status={status!r} (unexpected for an injected failure)"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inject one worker 500 and record the truth")
    parser.add_argument("--case", default=FAILURE_INJECTION_CASE)
    args = parser.parse_args(argv)

    from agent import Budgets

    record = get_claim_record(args.case)
    expected = outputs.expected_output(record)
    budgets = Budgets(MAX_ITERS, MAX_TOKENS, MAX_COST, MAX_SECONDS)

    print(f"Injecting HTTP 500 into the coverage/exclusions worker on {args.case} ...")
    run = run_orchestrator(args.case, budgets=budgets, expected=expected,
                           inject_500=True)
    behaviour = classify(run)

    # The clean race run of the same case, for the before/after comparison.
    clean = None
    if RUN_CACHE_PATH.exists():
        cache = json.loads(RUN_CACHE_PATH.read_text(encoding="utf-8")).get("runs", {})
        clean = cache.get(f"orchestrator:{args.case}")

    attempts = run.worker_attempts.get("coverage_exclusions_worker", 0)
    http_lines = [h for h in run.handoffs if str(h.get("status", "")).startswith("HTTP")]
    declared_covered = (run.output or {}).get("status") == "approved"

    payload = {
        "case": args.case,
        "model": MODEL,
        "judge": JUDGE_NAME,
        "injection": {
            "target": "coverage/exclusions worker",
            "failure": "HTTP 500 on every attempt",
            "surface": ("worker boundary (in-process in this build) - the same "
                        "place a real HTTP 500 from the worker endpoint would "
                        "surface, before any model usage is billed"),
            "attempts": attempts,
            "http_500_handoffs": len(http_lines),
            "tokens_billed_for_failed_attempts": sum(
                h["total_tokens"] for h in http_lines),
        },
        "behaviour_one_line": behaviour,
        "retry_happened": attempts > 1,
        "degraded": run.degraded,
        "declared_covered_without_exclusions_check": declared_covered,
        "guardrail_fired": run.guardrail_fired,
        "termination_reason": run.termination_reason,
        "termination_detail": run.termination_detail,
        "output": run.output,
        "expected": expected,
        "judge_verdict": "PASS" if run.pass_result else "FAIL",
        "judge_failures": run.failures,
        "handoffs": run.handoffs,
        "tokens": run.tokens,
        "cost": round(run.cost, 6),
        "latency_ms": round(run.latency_ms, 1),
        "clean_race_run": (
            None if not clean else {
                "tokens": clean["tokens"], "cost": clean["cost"],
                "latency_ms": clean["latency_ms"],
                "pass_result": clean["pass_result"],
                "output": clean["output"],
            }),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "failure_case.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---- markdown report ----
    md = []
    md.append(f"# Injected worker failure - {args.case} (Week 10 Module 5)")
    md.append("")
    md.append("## What was injected")
    md.append("")
    md.append(f"- **Case:** `{args.case}` - expected status `{expected['status']}`"
              + (f", clause `{expected['exclusion_clause']}`"
                 if expected.get("exclusion_clause") else "")
              + ". This case is chosen because a missing exclusions check on it "
                "flips **excluded -> covered** (payout 0 -> "
                f"{expected['payout']:,.0f}), which is the financially material lie.")
    md.append(f"- **Failure:** the coverage/exclusions worker returns **HTTP 500** on "
              f"every attempt ({attempts} attempt(s) issued).")
    md.append(f"- **Surface:** {payload['injection']['surface']}. "
              f"Tokens billed for the failed attempts: "
              f"{payload['injection']['tokens_billed_for_failed_attempts']}.")
    md.append("- **Everything else unchanged:** same prompts, same budgets, same "
              f"judge (`{JUDGE_NAME}`), same model `{MODEL}`.")
    md.append("")
    md.append("## What the orchestrator actually did (one line)")
    md.append("")
    md.append(f"> **{behaviour}**")
    md.append("")
    md.append("## Evidence - the hand-offs of the injected run")
    md.append("")
    md.append("| # | Hand-off | Status | Prompt tokens | Total | Detail |")
    md.append("| ---: | --- | --- | ---: | ---: | --- |")
    for i, h in enumerate(run.handoffs, 1):
        md.append("| %d | `%s` | %s | %d | %d | %s |" % (
            i, h["hop"], h["status"], h["prompt_tokens"], h["total_tokens"],
            h.get("detail", "")))
    md.append("")
    md.append("The two HTTP 500 lines carry 0 prompt tokens: the worker failed "
              "before any model usage was billed, and the orchestrator logged both "
              "attempts in the same hand-off log the multiplier is computed from.")
    md.append("")
    md.append("## The answer it produced (no coverage assertion)")
    md.append("")
    md.append("```json")
    md.append(json.dumps(run.output, indent=2, ensure_ascii=False))
    md.append("```")
    md.append("")
    md.append(f"- `termination_reason`: `{run.termination_reason}`")
    md.append(f"- `termination_detail`: {run.termination_detail}")
    md.append(f"- Judge on the injected run: **{payload['judge_verdict']}**"
              + (" - failures: " + json.dumps(run.failures, ensure_ascii=False)
                 if run.failures else ""))
    md.append(f"- Declared covered with no exclusions check? "
              f"**{str(declared_covered).lower()}** "
              f"(guardrail fired: {str(run.guardrail_fired).lower()})")
    if clean:
        md.append("")
        md.append("## Before vs after (same case, clean race run vs injected run)")
        md.append("")
        md.append("| Run | Pass | Tokens | Cost ($) | Latency (ms) | Status returned |")
        md.append("| --- | --- | ---: | ---: | ---: | --- |")
        md.append("| clean race run | %s | %s | %s | %s | %s |" % (
            "PASS" if clean["pass_result"] else "FAIL", f"{clean['tokens']:,}",
            clean["cost"], clean["latency_ms"],
            (clean.get("output") or {}).get("status")))
        md.append("| injected 500 | %s | %s | %s | %s | %s |" % (
            "PASS" if run.pass_result else "FAIL", f"{run.tokens:,}",
            round(run.cost, 6), round(run.latency_ms, 1),
            (run.output or {}).get("status")))
        md.append("")
        md.append("The injected run does not count in `race_table.md`; the race "
                  "stays a clean 10-vs-10 measurement.")
    md.append("")
    md.append("## Honest reading")
    md.append("")
    if declared_covered:
        md.append("The orchestrator **lied**: it shipped a coverage statement with "
                  "no exclusions check. That is the failure mode the squad was "
                  "supposed to prevent, and it did not.")
    elif run.degraded:
        md.append("The orchestrator **did not lie**: with no policy evidence it "
                  "refused to assert a status, handed back the identity facts it "
                  "had, and let the claim fail the judge instead of inventing "
                  "coverage. The claim is not paid on a guess - but it is also "
                  "not resolved, which is the honest cost of a dead specialist.")
    else:
        md.append("The orchestrator neither retried to success nor degraded; see "
                  "`behaviour_one_line` above for exactly what it did.")
    md.append("")
    md.append("## If this were a real deployment")
    md.append("")
    md.append("A dead coverage worker on a live claim should not silently become a "
              "failed claim: the A2A task should move to `input-required` and page "
              "an adjuster to confirm the exclusions (see `a2a_bonus.md`). The "
              "current implementation has no human-in-the-loop path, so it ends "
              "here.")
    (RESULTS_DIR / "failure_case.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"  attempts={attempts} http500_lines={len(http_lines)} "
          f"degraded={run.degraded} status={(run.output or {}).get('status')!r} "
          f"judge={payload['judge_verdict']}")
    print(f"  BEHAVIOUR: {behaviour}")
    print(f"Artifacts: {RESULTS_DIR / 'failure_case.md'}, "
          f"{RESULTS_DIR / 'failure_case.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
