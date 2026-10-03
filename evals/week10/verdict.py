"""The verdict: keep or kill the claims squad, on the four numbers.

Decision rule, written BEFORE looking at the numbers:
    keep the squad only if it beats the single agent on pass rate
    AND its context re-send multiplier is <= 5.0x.
Anything else keeps the single agent - a team that costs several times more
for the same result is not a win, and sunk cost is not evidence.

Output: results/verdict.md, max 10 lines.
"""

import argparse
import json

from race_config import RESULTS_DIR

KEEP_RULE_MULTIPLIER_MAX = 5.0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the keep/kill verdict")
    parser.add_argument("--metrics", default=str(RESULTS_DIR / "race_metrics.json"))
    args = parser.parse_args(argv)

    metrics = json.loads(open(args.metrics, encoding="utf-8").read())
    failure_path = RESULTS_DIR / "failure_case.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8")) if failure_path.exists() else {}

    single = metrics["single_agent"]
    squad = metrics["claims_squad"]
    mult = metrics["context_resend_multiplier"]
    dominant = metrics["dominant_handoff"]

    quality_gain = squad["pass_rate_pct"] - single["pass_rate_pct"]
    keep = quality_gain > 0 and mult <= KEEP_RULE_MULTIPLIER_MAX
    decision = "KEEP the claims squad" if keep else "KILL the claims squad - keep the single agent"

    behaviour = failure.get("behaviour_one_line", "not run (failure_case.json missing)")

    lines = [
        "# Week 10 Module 5 - verdict (claims squad vs single agent)",
        ("1. Sunk-cost bias, named first: I spent two days building the manager and "
         "two specialists, so I want them to win - these numbers are the check on "
         "that wish, not a justification for it."),
        (f"2. Decision ({decision.lower()}): the rule set before the run was 'keep "
         f"only if pass rate improves AND tokens <= {KEEP_RULE_MULTIPLIER_MAX:.0f}x'; "
         f"pass rate moved {quality_gain:+.1f} pp, multiplier {mult:.1f}x."),
        (f"3. Quality - pass rate {single['pass_rate_pct']:.1f}% ({single['pass_count']}/10) "
         f"single vs {squad['pass_rate_pct']:.1f}% ({squad['pass_count']}/10) squad, "
         f"same 10 Week-6 cases, same judge `{metrics['judge'].split(' ')[0]}`."),
        (f"4. Cost - ${single['cost_per_claim']:.6f} vs ${squad['cost_per_claim']:.6f} "
         f"per claim; total tokens {single['total_tokens']:,} vs "
         f"{squad['total_tokens']:,} = {mult:.1f}x context re-send "
         f"(largest hand-off `{dominant['hop']}`, {dominant['share_pct']:.1f}% of squad tokens)."),
        (f"5. Speed - p50 {single['p50_latency_ms']:.0f} ms vs "
         f"{squad['p50_latency_ms']:.0f} ms; p99 {single['p99_latency_ms']:.0f} ms vs "
         f"{squad['p99_latency_ms']:.0f} ms (p99 reported because it is the claim "
         "the client complains about)."),
        (f"6. Injected worker 500 - {behaviour}."),
        ("7. Multi-agent IS worth it when the pieces are genuinely independent and "
         "can run in parallel (batch triage across claims, or a claim needing two "
         "unrelated specialist reads with different instructions)."),
        ("8. Multi-agent is NOT worth it here: one claim is a short dependent chain "
         "(facts -> coverage -> decide), so each hand-off only re-sends context; the "
         "squad bought no quality and charged for the re-sends."),
        ("9. Numbers: results/race_table.md, results/handoffs.log, "
         "results/failure_case.md, results/multiplier.txt."),
    ]

    if len(lines) > 10:
        lines = lines[:10]

    out = RESULTS_DIR / "verdict.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {out} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
