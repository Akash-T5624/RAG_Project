import csv
import json
import time
from pathlib import Path
from statistics import median

import outputs
from agent import Budgets, run_agent
from claims import dataset_report, race_claim_records
from config import (
    MAX_COST,
    MAX_ITERS,
    MAX_SECONDS,
    MAX_TOKENS,
    MODEL,
    PRICE_PER_1M_INPUT,
    PRICE_PER_1M_OUTPUT,
    PRICING_SOURCE,
    REPO_ROOT,
    RESULTS_DIR,
)

RACE_CSV_PATH = REPO_ROOT / "race.csv"


def _round(v, digits=4):
    if isinstance(v, float):
        return round(v, digits)
    return v


def run_one(system, claim_id, budgets, expected, cache):
    key = f"{system}:{claim_id}"
    if key in cache:
        print(f"  cached {key}")
        return cache[key]

    if system == "agent":
        run = run_agent(claim_id, budgets=budgets, expected=expected)
    else:
        from workflow import run_workflow

        run = run_workflow(claim_id, expected=expected)

    row = {
        "claim_id": claim_id,
        "system": system,
        "pass": run.pass_result,
        "latency_ms": _round(run.latency_ms, 1),
        "tokens": run.tokens,
        "cost": _round(run.cost, 6),
        "termination_reason": run.termination_reason,
        "iterations": run.iterations,
        "expected_status": expected["status"],
        "actual_status": (run.output or {}).get("status"),
        "expected_payout": expected["payout"],
        "actual_payout": ((run.output or {}).get("payout") if run.output else None),
        "expected_excess": expected["excess"],
        "actual_excess": ((run.output or {}).get("excess") if run.output else None),
        "error": "|".join(run.failures) if run.failures else "",
        "model": run.model,
    }
    cache[key] = row
    print(f"  {system} {claim_id}: PASS={row['pass']} "
          f"lat={row['latency_ms']}ms tokens={row['tokens']} "
          f"iters={row['iterations']} term={row['termination_reason']}")
    return row


def aggregate(rows):
    rows = list(rows)
    n = len(rows) or 1
    passes = sum(1 for r in rows if r["pass"])
    latencies = [float(r["latency_ms"]) for r in rows]
    tokens = sum(int(r["tokens"]) for r in rows)
    cost = sum(float(r["cost"]) for r in rows)
    return {
        "claims_run": len(rows),
        "pass_rate_pct": _round(passes / len(rows) * 100, 1) if rows else 0.0,
        "p50_latency_ms": _round(median(latencies), 1) if latencies else 0.0,
        "total_tokens": tokens,
        "cost_per_claim": _round(cost / n, 6),
        "total_cost": _round(cost, 6),
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Agent vs workflow race")
    parser.add_argument("--max-iters", type=int, default=MAX_ITERS)
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    parser.add_argument("--max-cost", type=float, default=MAX_COST)
    parser.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    parser.add_argument("--nocache", action="store_true", help="ignore cached results")
    args = parser.parse_args(argv)

    budgets = Budgets(args.max_iters, args.max_tokens, args.max_cost, args.max_seconds)
    claims = race_claim_records()
    expected = {c["claim_id"]: outputs.expected_output(c) for c in claims}

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    cache = {}
    if not args.nocache and (RESULTS_DIR / "per_claim_results.json").exists():
        cached_rows = json.loads(
            (RESULTS_DIR / "per_claim_results.json").read_text(encoding="utf-8")
        )
        for r in cached_rows["rows"]:
            cache[f"{r['system']}:{r['claim_id']}"] = r

    report = dataset_report()
    print("=" * 60)
    print("WEEK 7 MODULE 4 - AGENT vs FIXED WORKFLOW RACE")
    print("=" * 60)
    print(f"Model: {MODEL}")
    print(f"Budget: iters={args.max_iters} tokens={args.max_tokens} "
          f"cost=${args.max_cost} seconds={args.max_seconds}")
    print(f"Claims: {len(claims)} ({', '.join(c['claim_id'] for c in claims)})")
    print(f"Pricing assumption: input ${PRICE_PER_1M_INPUT}/1M, output "
          f"${PRICE_PER_1M_OUTPUT}/1M ({PRICING_SOURCE})")
    print()

    rows = []
    for system in ("agent", "workflow"):
        print(f"Running {system.upper()}...")
        for c in claims:
            rows.append(run_one(system, c["claim_id"], budgets, expected[c["claim_id"]], cache))
            # Pace guard: gives the shared Groq key's token/request windows time
            # to refill between claims so rate limits distort latency least.
            time.sleep(1.0)

    # ---- Write race.csv (reproducibility master) ----
    csv_columns = [
        "claim_id", "system", "pass", "latency_ms", "tokens", "cost",
        "termination_reason", "iterations", "expected_status", "actual_status",
        "expected_payout", "actual_payout", "expected_excess", "actual_excess",
        "error",
    ]
    with open(RACE_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in csv_columns})
    print(f"\nWrote {RACE_CSV_PATH}")

    # ---- Per-claim results (json) ----
    per_claim = {
        "model": MODEL,
        "claims": len(claims),
        "budgets": {
            "max_iters": args.max_iters, "max_tokens": args.max_tokens,
            "max_cost": args.max_cost, "max_seconds": args.max_seconds,
        },
        "pricing_assumption": {
            "input_usd_per_1m": PRICE_PER_1M_INPUT,
            "output_usd_per_1m": PRICE_PER_1M_OUTPUT,
            "source": PRICING_SOURCE,
        },
        "dataset_report": report,
        "rows": rows,
    }
    (RESULTS_DIR / "per_claim_results.json").write_text(
        json.dumps(per_claim, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ---- Aggregates ----
    agent_agg = aggregate(r for r in rows if r["system"] == "agent")
    work_agg = aggregate(r for r in rows if r["system"] == "workflow")
    metrics = {
        "model": MODEL,
        "agent": agent_agg,
        "workflow": work_agg,
        "pricing_assumption": per_claim["pricing_assumption"],
        "claim_amount_assumption": report["claim_amount_assumption"],
    }
    (RESULTS_DIR / "race_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # ---- Final comparison table (markdown) ----
    def fmt(v):
        if isinstance(v, float):
            return f"{v:.4f}" if v and v < 1 else f"{v:.1f}"
        return str(v)

    md = []
    md.append("## Week 7 Module 4 - Final comparison: Agent vs Fixed Workflow")
    md.append("")
    md.append("Model: `%s`  ·  Pricing: input $%s/1M, output $%s/1M (Groq list price)"
              % (MODEL, PRICE_PER_1M_INPUT, PRICE_PER_1M_OUTPUT))
    md.append("Evaluation data: `evals/week6/eval_set.jsonl` (27 claims); race set selects 10 "
              "by the documented deterministic rule.")
    md.append("")
    md.append("| Metric | Agent | Fixed Workflow |")
    md.append("| --- | ---: | ---: |")
    md.append("| Pass rate | %.1f%% | %.1f%% |" % (
        agent_agg["pass_rate_pct"], work_agg["pass_rate_pct"]))
    md.append("| p50 latency (ms) | %s | %s |" % (
        agent_agg["p50_latency_ms"], work_agg["p50_latency_ms"]))
    md.append("| Total tokens | %s | %s |" % (
        agent_agg["total_tokens"], work_agg["total_tokens"]))
    md.append("| Cost per claim ($) | %s | %s |" % (
        agent_agg["cost_per_claim"], work_agg["cost_per_claim"]))
    md.append("")
    md.append("### Per-claim results")
    md.append("")
    md.append("| Claim ID | Agent Pass | Agent Latency (ms) | Agent Tokens | Agent Cost ($) | "
              "Workflow Pass | Workflow Latency (ms) | Workflow Tokens | Workflow Cost ($) |")
    md.append("| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |")
    by_claim = {}
    for r in rows:
        by_claim.setdefault(r["claim_id"], {})[r["system"]] = r
    for cid in [c["claim_id"] for c in claims]:
        a, w = by_claim[cid]["agent"], by_claim[cid]["workflow"]
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            cid,
            "PASS" if a["pass"] else "FAIL", a["latency_ms"], a["tokens"], a["cost"],
            "PASS" if w["pass"] else "FAIL", w["latency_ms"], w["tokens"], w["cost"]))
    (RESULTS_DIR / "comparison_table.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # ---- Verdict inputs ----
    (RESULTS_DIR / "verdict_inputs.json").write_text(
        json.dumps({
            "comparison": {
                "agent": agent_agg, "workflow": work_agg,
                "delta_pass_rate_pp": _round(
                    agent_agg["pass_rate_pct"] - work_agg["pass_rate_pct"], 1),
                "delta_p50_latency_ms": _round(
                    work_agg["p50_latency_ms"] - agent_agg["p50_latency_ms"], 1),
            },
            "reasons": {r["claim_id"]: r["termination_reason"] for r in rows},
            "branching_claim_ids": [
                c["claim_id"] for c in claims
                if c["expect"].get("exclusion_cite_required")
            ],
            "branching_counterfactual": (
                "For claims where the exclusion-clause citation in the notes is the "
                "deciding fact (e.g. W6-002 water ingress -> Exc-7.4), the workflow's "
                "coded decision reaches the same status as the provided labels, so no "
                "benefit from agent branching was measured on this dataset."),
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n=== AGGREGATES ===")
    for system, agg in (("Agent", agent_agg), ("Workflow", work_agg)):
        print(f"{system}: pass_rate={agg['pass_rate_pct']}%  "
              f"p50_latency={agg['p50_latency_ms']}ms  "
              f"tokens={agg['total_tokens']}  "
              f"cost/claim=${agg['cost_per_claim']}")
    print(f"\nArtifacts: {RACE_CSV_PATH}, {RESULTS_DIR / 'race_metrics.json'}, "
          f"{RESULTS_DIR / 'comparison_table.md'}")


if __name__ == "__main__":
    raise SystemExit(main())