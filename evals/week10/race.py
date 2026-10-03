"""Week 10 Module 5 — the race: single agent vs claims squad.

Same 10 Week-6 eval cases.  Same judge.  Same model, same key, same pricing.
Nothing else is shared: arm A is the single ReAct agent you already had, arm B
is the orchestrator + two narrow specialists.

Reported for BOTH arms (the four numbers the assignment asks for):
    pass rate, p50 latency, p99 latency, total tokens, cost per claim
plus the context re-send multiplier and the hand-off it is attributed to.

Run:  cd evals/week10 && python race.py [--arm both|single|orchestrator] [--nocache]
"""

import argparse
import json
import math
import time

# race_config first: it puts the week-7 package on sys.path, which is where
# `outputs`, `claims`, `agent` etc. live.
from race_config import (
    HANDOFF_LOG_PATH,
    JUDGE_NAME,
    MAX_COST,
    MAX_ITERS,
    MAX_SECONDS,
    MAX_TOKENS,
    MODEL,
    PRICE_PER_1M_INPUT,
    PRICE_PER_1M_OUTPUT,
    PRICING_SOURCE,
    RACE_CLAIM_IDS,
    RESULTS_DIR,
    RUN_CACHE_PATH,
)

import outputs
from claims import race_claim_records
from handoffs import dominant_hop, hop_totals, write_log
from orchestrator import run_orchestrator
from single_agent import SYSTEM_NAME as SINGLE_ARM
from single_agent import run_single, to_record as single_to_record

MULTI_ARM = "orchestrator"
ARMS = (SINGLE_ARM, MULTI_ARM)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def percentile(values, p):
    """Linear-interpolation percentile (numpy's default method).

    With 10 samples p99 sits between the 9th and 10th sorted value and is
    therefore close to - but not equal to - the max.  Reported deliberately:
    the p99 is the claim the client complains about.
    """
    if not values:
        return 0.0
    s = sorted(float(v) for v in values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _round(v, digits=1):
    return round(float(v), digits) if isinstance(v, (int, float)) else v


def load_cache(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("meta", {}).get("model") != MODEL:
        print("  cache model differs from current MODEL - ignoring cache")
        return {}
    if data.get("meta", {}).get("cases") != RACE_CLAIM_IDS:
        print("  cache case list differs - ignoring cache")
        return {}
    return data.get("runs", {})


def save_cache(path, runs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "meta": {"model": MODEL, "cases": RACE_CLAIM_IDS,
                 "judge": JUDGE_NAME,
                 "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")},
        "runs": runs,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


def write_claim_log(system, record, expected):
    """One log file per claim per arm (the multi arm also lands in handoffs.log)."""
    from orchestrator import OrchestratorRun
    import agent as week7_agent

    LOGS = RESULTS_DIR.parent / "logs"
    LOGS.mkdir(parents=True, exist_ok=True)
    if system == MULTI_ARM:
        run = OrchestratorRun(claim_id=record["claim_id"])
        for key, value in record.items():
            if hasattr(run, key):
                setattr(run, key, value)
        lines = run.to_log_lines(expected)
        name = f"orchestrator_{record['claim_id']}.log"
    else:
        run = week7_agent.AgentRun(claim_id=record["claim_id"])
        for key, value in record.items():
            if hasattr(run, key):
                setattr(run, key, value)
        lines = run.to_log_lines(expected)
        name = f"single_{record['claim_id']}.log"
    (LOGS / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# running one claim on one arm
# ---------------------------------------------------------------------------
def run_one(system, claim_id, expected, cache):
    key = f"{system}:{claim_id}"
    if key in cache:
        print(f"  cached {key}")
        return cache[key]

    if system == SINGLE_ARM:
        budgets = week7_budgets()
        run = run_single(claim_id, budgets=budgets, expected=expected)
        record = single_to_record(run)
    else:
        from agent import Budgets

        budgets = Budgets(MAX_ITERS, MAX_TOKENS, MAX_COST, MAX_SECONDS)
        run = run_orchestrator(claim_id, budgets=budgets, expected=expected)
        record = run.to_dict()

    cache[key] = record
    # A run that produced no answer (wall-clock / budget / llm error) is a FAIL,
    # never an unreported blank: it counts against the pass rate.
    if record.get("pass_result") is None:
        record["pass_result"] = False
        record["failures"] = list(record.get("failures") or []) + [
            f"no validated output (termination={record.get('termination_reason')})"]
    write_claim_log(system, record, expected)
    print(f"  {system} {claim_id}: PASS={record['pass_result']} "
          f"lat={record['latency_ms']}ms tokens={record['tokens']} "
          f"term={record['termination_reason']} "
          f"handoffs={len(record.get('handoffs') or [])}")
    return record


def week7_budgets():
    from agent import Budgets

    return Budgets.from_config()


# ---------------------------------------------------------------------------
# aggregation — the four numbers
# ---------------------------------------------------------------------------
def aggregate(rows):
    rows = [r for r in rows if r is not None]
    n = len(rows) or 1
    passes = sum(1 for r in rows if r.get("pass_result"))
    latencies = [float(r["latency_ms"]) for r in rows]
    tokens = sum(int(r["tokens"]) for r in rows)
    prompt = sum(int(r.get("prompt_tokens") or 0) for r in rows)
    completion = sum(int(r.get("completion_tokens") or 0) for r in rows)
    cost = sum(float(r["cost"]) for r in rows)
    return {
        "claims_run": len(rows),
        "pass_count": passes,
        "pass_rate_pct": _round(passes / n * 100, 1),
        "p50_latency_ms": _round(percentile(latencies, 50)),
        "p99_latency_ms": _round(percentile(latencies, 99)),
        "total_tokens": tokens,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_cost": _round(cost, 6),
        "cost_per_claim": _round(cost / n, 6),
    }


def winner(single_v, multi_v, higher_is_better):
    if single_v == multi_v:
        return "tie"
    better = (single_v > multi_v) == higher_is_better
    return "single" if better else "squad"


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def build_table(single_agg, multi_agg, multiplier, hop, hop_share, hop_tokens,
                rows, claims, handoff_totals):
    by_claim = {}
    for r in rows:
        by_claim.setdefault(r["claim_id"], {})[r["system"]] = r

    md = []
    md.append("## Week 10 Module 5 - Claims squad (1 manager + 2 specialists) vs single agent")
    md.append("")
    md.append(f"- Model: `{MODEL}` - one model id, one key, both arms")
    md.append(f"- Pricing: input ${PRICE_PER_1M_INPUT}/1M, output ${PRICE_PER_1M_OUTPUT}/1M "
              f"({PRICING_SOURCE})")
    md.append("- Eval set: `evals/week6/eval_set.jsonl` - the **same 10 Week-6 cases** for "
              "both arms, no new cases written: "
              + ", ".join(f"`{c}`" for c in RACE_CLAIM_IDS))
    md.append(f"- Judge: `{JUDGE_NAME}` - one function, both arms")
    md.append(f"- Arm A = single ReAct agent (week 7, unchanged). Arm B = orchestrator + "
              f"`coverage/exclusions worker` + `adjuster-note summary worker`.")
    md.append("")
    md.append("| Metric | Single agent (A) | Claims squad (B) | Winner |")
    md.append("| --- | ---: | ---: | --- |")
    md.append("| Pass rate (same judge, same 10 cases) | %.1f%% (%d/10) | %.1f%% (%d/10) | %s |" % (
        single_agg["pass_rate_pct"], single_agg["pass_count"],
        multi_agg["pass_rate_pct"], multi_agg["pass_count"],
        winner(single_agg["pass_rate_pct"], multi_agg["pass_rate_pct"], True)))
    md.append("| p50 latency | %.1f ms | %.1f ms | %s |" % (
        single_agg["p50_latency_ms"], multi_agg["p50_latency_ms"],
        winner(single_agg["p50_latency_ms"], multi_agg["p50_latency_ms"], False)))
    md.append("| p99 latency | %.1f ms | %.1f ms | %s |" % (
        single_agg["p99_latency_ms"], multi_agg["p99_latency_ms"],
        winner(single_agg["p99_latency_ms"], multi_agg["p99_latency_ms"], False)))
    md.append("| Total tokens | %s | %s | %s |" % (
        f"{single_agg['total_tokens']:,}", f"{multi_agg['total_tokens']:,}",
        winner(single_agg["total_tokens"], multi_agg["total_tokens"], False)))
    md.append("| Cost per claim | $%.6f | $%.6f | %s |" % (
        single_agg["cost_per_claim"], multi_agg["cost_per_claim"],
        winner(single_agg["cost_per_claim"], multi_agg["cost_per_claim"], False)))
    md.append("")
    md.append("**Context re-send multiplier: %.1fx** (%s squad tokens / %s single tokens)."
              % (multiplier, f"{multi_agg['total_tokens']:,}",
                 f"{single_agg['total_tokens']:,}"))
    if hop:
        md.append("**Largest single hand-off:** `%s` - %s tokens = %.1f%% of all "
                  "squad-arm tokens (from `handoffs.log`)." % (
                      hop, f"{hop_tokens:,}", hop_share))
    md.append("")
    md.append("### Hand-off totals (squad arm, from `handoffs.log`)")
    md.append("")
    md.append("| Hand-off | Model calls | Prompt tokens (re-sent context) | "
              "Completion tokens | Total | Share of squad tokens |")
    md.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for name, bucket in sorted(handoff_totals.items(),
                               key=lambda kv: -kv[1]["total_tokens"]):
        share = bucket["total_tokens"] / max(1, multi_agg["total_tokens"]) * 100.0
        md.append("| `%s` | %d | %s | %s | %s | %.1f%% |" % (
            name, bucket["calls"], f"{bucket['prompt_tokens']:,}",
            f"{bucket['completion_tokens']:,}",
            f"{bucket['total_tokens']:,}", share))
    md.append("")
    md.append("### Per-claim results (same 10 cases)")
    md.append("")
    md.append("| Claim | Expected status | A pass | A latency (ms) | A tokens | A cost | "
              "B pass | B latency (ms) | B tokens | B cost |")
    md.append("| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |")
    for c in claims:
        cid = c["claim_id"]
        a = by_claim.get(cid, {}).get(SINGLE_ARM, {})
        b = by_claim.get(cid, {}).get(MULTI_ARM, {})
        md.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            cid, c.get("expected_status", ""),
            "PASS" if a.get("pass_result") else "FAIL",
            a.get("latency_ms"), f"{a.get('tokens', 0):,}", a.get("cost"),
            "PASS" if b.get("pass_result") else "FAIL",
            b.get("latency_ms"), f"{b.get('tokens', 0):,}", b.get("cost")))
    md.append("")
    md.append("Notes: p50/p99 use linear interpolation over the 10 per-claim latencies "
              "(p99 with n=10 sits between the 9th and 10th value - it is reported, not "
              "hidden). Latency is wall-clock for the whole claim, including the inter-call "
              "pace both arms pay against the rate-limited key.")
    return "\n".join(md) + "\n"


# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="Week 10 race: single vs squad")
    parser.add_argument("--arm", choices=[*ARMS, "both"], default="both")
    parser.add_argument("--nocache", action="store_true",
                        help="ignore results/run_cache.json and re-run every claim")
    parser.add_argument("--rerun-arm", choices=list(ARMS), default=None,
                        help="drop the cached rows of one arm and re-run just that arm")
    parser.add_argument("--only", nargs="+", metavar="CLAIM_ID", default=None,
                        help="drop and re-run only these claim ids (any arm)")
    args = parser.parse_args(argv)

    arms = ARMS if args.arm == "both" else (args.arm,)
    all_claims = race_claim_records()
    expected = {c["claim_id"]: outputs.expected_output(c) for c in all_claims}
    expected_status = {cid: expected[cid]["status"] for cid in expected}
    run_claims = all_claims
    if args.only:
        run_claims = [c for c in all_claims if c["claim_id"] in args.only]
        if not run_claims:
            raise SystemExit(f"--only: none of {args.only} are in the race set")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    cache = {} if args.nocache else load_cache(RUN_CACHE_PATH)
    if args.rerun_arm:
        dropped = [k for k in cache if k.startswith(f"{args.rerun_arm}:")]
        for key in dropped:
            cache.pop(key, None)
        print(f"--rerun-arm {args.rerun_arm}: dropped {len(dropped)} cached rows")
    if args.only:
        dropped = [k for k in cache
                   if k.split(":", 1)[0] in arms and k.split(":", 1)[1] in args.only]
        for key in dropped:
            cache.pop(key, None)
        print(f"--only {', '.join(args.only)}: dropped {len(dropped)} cached rows")
    print()

    print("=" * 68)
    print("WEEK 10 MODULE 5 - SINGLE AGENT vs CLAIMS SQUAD (same 10 cases, same judge)")
    print("=" * 68)
    print(f"Model: {MODEL}")
    print(f"Judge: {JUDGE_NAME}")
    print(f"Cases: {', '.join(RACE_CLAIM_IDS)}")
    print(f"Pricing: input ${PRICE_PER_1M_INPUT}/1M, output ${PRICE_PER_1M_OUTPUT}/1M")
    print(f"Arms: {', '.join(arms)}")
    print()

    rows = []
    for system in arms:
        print(f"Running {system}...")
        for c in run_claims:
            rows.append(run_one(system, c["claim_id"], expected[c["claim_id"]], cache))
            save_cache(RUN_CACHE_PATH, cache)     # crash-safe: every claim persisted
            time.sleep(1.0)
        print()

    # Rows from cache for arms that were not re-run this invocation.
    all_rows = [cache[k] for k in sorted(cache)
                if k.split(":", 1)[0] in ARMS]
    all_rows.sort(key=lambda r: (RACE_CLAIM_IDS.index(r["claim_id"])
                                 if r["claim_id"] in RACE_CLAIM_IDS else 99,
                                 r["system"]))

    single_agg = aggregate([r for r in all_rows if r["system"] == SINGLE_ARM])
    multi_agg = aggregate([r for r in all_rows if r["system"] == MULTI_ARM])

    # Hand-off log = every model call of the squad arm, per claim.
    handoff_records = []
    for cid in RACE_CLAIM_IDS:
        rec = cache.get(f"{MULTI_ARM}:{cid}")
        if rec:
            handoff_records.extend(rec.get("handoffs") or [])
    write_log(HANDOFF_LOG_PATH, handoff_records)

    # Multiplier + attribution (the log is the source of the attribution).
    if single_agg["total_tokens"]:
        multiplier = round(multi_agg["total_tokens"] / single_agg["total_tokens"], 1)
    else:
        multiplier = 0.0
    hop, hop_share, hop_tokens = dominant_hop(handoff_records, multi_agg["total_tokens"])
    totals = hop_totals(handoff_records)

    # Sanity check: the squad's tokens must equal the tokens in the log.
    logged = sum(r["total_tokens"] for r in handoff_records)
    token_drift = multi_agg["total_tokens"] - logged
    if abs(token_drift) > 0:
        print(f"  NOTE: squad tokens {multi_agg['total_tokens']} vs logged "
              f"{logged} (drift {token_drift})")

    metrics = {
        "model": MODEL,
        "judge": JUDGE_NAME,
        "cases": RACE_CLAIM_IDS,
        "pricing_assumption": {
            "input_usd_per_1m": PRICE_PER_1M_INPUT,
            "output_usd_per_1m": PRICE_PER_1M_OUTPUT,
            "source": PRICING_SOURCE,
        },
        "single_agent": single_agg,
        "claims_squad": multi_agg,
        "context_resend_multiplier": multiplier,
        "dominant_handoff": {"hop": hop, "share_pct": hop_share,
                             "tokens": hop_tokens},
        "handoff_totals": {k: v for k, v in totals.items()},
        "logged_squad_tokens": logged,
        "token_drift_vs_log": token_drift,
    }
    (RESULTS_DIR / "race_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    per_claim = {
        "model": MODEL, "judge": JUDGE_NAME, "cases": RACE_CLAIM_IDS,
        "expected_status": expected_status, "runs": cache,
    }
    (RESULTS_DIR / "per_claim.json").write_text(
        json.dumps(per_claim, indent=2, ensure_ascii=False), encoding="utf-8")

    claims_for_table = [
        {"claim_id": cid, "expected_status": expected_status[cid]}
        for cid in RACE_CLAIM_IDS
    ]
    table = build_table(single_agg, multi_agg, multiplier, hop, hop_share,
                        hop_tokens, all_rows, claims_for_table, totals)
    (RESULTS_DIR / "race_table.md").write_text(table, encoding="utf-8")

    multiplier_line = (
        "Context re-send multiplier: %.1fx (squad %s tokens / single %s tokens). "
        "Largest single hand-off: `%s` - %s tokens = %.1f%% of all squad-arm tokens."
        % (multiplier, f"{multi_agg['total_tokens']:,}",
           f"{single_agg['total_tokens']:,}", hop or "n/a",
           f"{hop_tokens:,}", hop_share)
    )
    (RESULTS_DIR / "multiplier.txt").write_text(multiplier_line + "\n", encoding="utf-8")

    print("=== THE FOUR NUMBERS ===")
    print(f"{'Metric':<24}{'single':>18}{'squad':>18}")
    print(f"{'pass rate':<24}{single_agg['pass_rate_pct']:>17.1f}%"
          f"{multi_agg['pass_rate_pct']:>17.1f}%")
    print(f"{'p50 latency (ms)':<24}{single_agg['p50_latency_ms']:>18.1f}"
          f"{multi_agg['p50_latency_ms']:>18.1f}")
    print(f"{'p99 latency (ms)':<24}{single_agg['p99_latency_ms']:>18.1f}"
          f"{multi_agg['p99_latency_ms']:>18.1f}")
    print(f"{'total tokens':<24}{single_agg['total_tokens']:>18,}"
          f"{multi_agg['total_tokens']:>18,}")
    print(f"{'cost per claim ($)':<24}{single_agg['cost_per_claim']:>18.6f}"
          f"{multi_agg['cost_per_claim']:>18.6f}")
    print()
    print(multiplier_line)
    print()
    print(f"Artifacts: {RESULTS_DIR / 'race_table.md'}, "
          f"{HANDOFF_LOG_PATH}, {RESULTS_DIR / 'race_metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
