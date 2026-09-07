import argparse
import json
from pathlib import Path

import outputs
from agent import Budgets, run_agent
from claims import get_claim_record
from config import REPO_ROOT, RACE_CLAIM_IDS

BUDGET_LOG_PATH = REPO_ROOT / "budget_termination.log"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claim_id", nargs="?", default="W6-002",
                        help="claim to run (default W6-002, a branching claim)")
    parser.add_argument("--budget", choices=["max_iterations", "max_tokens",
                                             "max_cost", "wall_clock"],
                        default="max_iterations")
    parser.add_argument("--max-iters", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--max-cost", type=float, default=None)
    parser.add_argument("--max-seconds", type=float, default=None)
    args = parser.parse_args(argv)

    # Force one tiny budget; keep the others generous so only the chosen one fires.
    if args.budget == "max_iterations":
        b = Budgets(max_iters=args.max_iters or 1,
                    max_tokens=args.max_tokens or 20000,
                    max_cost=args.max_cost or 0.05,
                    max_seconds=args.max_seconds or 120.0)
    elif args.budget == "max_tokens":
        b = Budgets(max_iters=args.max_iters or 8,
                    max_tokens=args.max_tokens or 250,
                    max_cost=args.max_cost or 0.05,
                    max_seconds=args.max_seconds or 120.0)
    elif args.budget == "max_cost":
        b = Budgets(max_iters=args.max_iters or 8,
                    max_tokens=args.max_tokens or 20000,
                    max_cost=args.max_cost or 0.0000005,
                    max_seconds=args.max_seconds or 120.0)
    else:  # wall_clock
        b = Budgets(max_iters=args.max_iters or 8,
                    max_tokens=args.max_tokens or 20000,
                    max_cost=args.max_cost or 0.05,
                    max_seconds=args.max_seconds or 0.6)

    record = get_claim_record(args.claim_id)
    expected = outputs.expected_output(record)
    run = run_agent(args.claim_id, budgets=b, expected=expected)

    lines = []
    lines.append("=" * 60)
    lines.append("BUDGET TERMINATION TEST - agent run that HITS a budget")
    lines.append("=" * 60)
    lines.append(f"Claim id: {args.claim_id}")
    lines.append(f"Claim number: {record['claim_number']}")
    lines.append(f"Budget configured: {args.budget}")
    lines.append(f"max_iters={b.max_iters}, max_tokens={b.max_tokens}, "
                 f"max_cost=${b.max_cost}, max_seconds={b.max_seconds}")
    lines.append(f"Budget reached: {args.budget}")
    if args.budget == "max_iterations":
        lines.append(f"Current iterations: {run.iterations}")
        lines.append(f"Maximum iterations: {b.max_iters}")
    elif args.budget == "max_tokens":
        lines.append(f"Current tokens: {run.tokens}")
        lines.append(f"Maximum tokens: {b.max_tokens}")
    elif args.budget == "max_cost":
        lines.append(f"Current cost: ${run.cost:.6f}")
        lines.append(f"Maximum cost: ${b.max_cost:.6f}")
    else:
        lines.append(f"Current elapsed: {run.latency_ms / 1000.0:.2f}s")
        lines.append(f"Maximum seconds: {b.max_seconds:.1f}")
    lines.append(f"Termination reason: {run.termination_reason}")
    lines.append("Agent terminated cleanly.")
    lines.append(f"Final state: iterations={run.iterations}, tokens={run.tokens}, "
                 f"cost=${run.cost:.6f}, latency_ms={run.latency_ms:.1f}, "
                 f"output={json.dumps(run.output) if run.output else None}")
    lines.append(f"Tool calls executed: {[s['tool'] for s in run.steps]}")
    lines.append("=" * 60)
    body = "\n".join(lines) + "\n"
    BUDGET_LOG_PATH.write_text(body, encoding="utf-8")
    print(body)
    print(f"\nWrote {BUDGET_LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())