"""Run the integrated eval suite (live model + live MCP server) and score it.

    python capstone/run.py eval                  # date filter ON  -> reports/suite_after.json
    python capstone/run.py eval --before         # date filter OFF -> reports/suite_before.json (the pre-fix behaviour)
Runs are cached per case (resume with the same command). Assertions run first; the judge runs last.
"""

import json
import statistics
import time

from capstone.agent.loop import run_agent
from capstone.checks import assertions, judge, taxonomy, trajectory
from capstone.core import llm, policies
from capstone.core.paths import REPORTS
from capstone.mcp_layer.mcp_client import McpSession


def path_for(mode):
    return REPORTS / f"suite_{mode}.json"


def to_record(run):
    tr = run.trace_record or {}
    return {
        "id": run.claim_id, "termination": run.termination, "detail": run.detail, "iterations": run.iterations,
        "tokens": run.tokens, "cost_usd": run.cost, "tools_called": run.tools_called,
        "steps": [{"tool": s["tool"], "args": s["args"], "status": s["status"]} for s in run.steps],
        "delivered": run.delivered, "guard_violations": run.guard_violations,
        "unretrieved_citations": run.unretrieved_citations, "args_overridden": run.args_overridden,
        "in_force_fact": run.in_force_fact, "referral": run.referral, "trace_id": run.trace.trace_id,
        "latency_ms": tr.get("latency_ms"), "trace_cost_usd": (tr.get("cost_usd") or {}).get("total"),
        "model": run.model, "trace": tr,
    }


def run_cases(mode="after", ids=None, fresh=False, log=print):
    if not llm.api_key_present():
        raise SystemExit("GROQ_API_KEY is not set (backend/.env). The eval needs the live model; nothing was run.")
    cases = policies.load_cases()
    ids = ids or list(cases)
    out = path_for(mode)
    REPORTS.mkdir(parents=True, exist_ok=True)
    done = {} if fresh or not out.exists() else json.loads(out.read_text(encoding="utf-8"))
    mcp = McpSession(env={"CAPSTONE_DATE_FILTER": "0" if mode == "before" else "1"})
    log(f"starting MCP server (date_filter={'OFF' if mode == 'before' else 'ON'}) ...")
    mcp.start()
    log(f"discovered tools: {[t['name'] for t in mcp.tools]}")
    try:
        for i, cid in enumerate(ids, 1):
            if cid in done:
                continue
            t = time.time()
            run = run_agent(cid, mcp)
            done[cid] = to_record(run)
            out.write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")
            log(f"[{i}/{len(ids)}] {cid}: {run.termination} cost=${run.cost:.5f} {time.time() - t:.0f}s trace={run.trace.trace_id}")
    finally:
        mcp.close()
    return done


def evaluate(records):
    cases = policies.load_cases()
    per_case, traj_rows, taxo = {}, [], []
    for cid, rec in records.items():
        case = cases[cid]
        a = assertions.run_all(case, rec)
        modes = taxonomy.classify(case, rec, a)
        tj = trajectory.judge_trajectory(case, rec)
        per_case[cid] = {"assertions": a, "modes": modes, "trajectory": tj, "slices": case["slices"], "trace_id": rec["trace_id"]}
        traj_rows.append({**tj, "outcome_pass": a["all_pass"], "id": cid})
        taxo.append({"id": cid, "modes": modes, "trace_id": rec["trace_id"]})
    n = len(per_case) or 1
    by_assertion = {name: round(sum(c["assertions"][name]["passed"] for c in per_case.values()) / n * 100, 1)
                    for name in list(assertions.ASSERTIONS) + list(assertions.INFORMATIONAL)}
    slices = {}
    for sl in sorted({s for c in per_case.values() for s in c["slices"]}):
        sub = [c for c in per_case.values() if sl in c["slices"]]
        slices[sl] = {"n": len(sub), "all_assertions_pass_pct": round(sum(c["assertions"]["all_pass"] for c in sub) / len(sub) * 100, 1),
                      "citation_version_in_force_pct": round(sum(c["assertions"]["citation_version_in_force"]["passed"] for c in sub) / len(sub) * 100, 1)}
    costs = [r["trace_cost_usd"] or 0.0 for r in records.values()]
    lat = [r["latency_ms"] or 0.0 for r in records.values()]
    return {
        "n": len(per_case),
        "overall_pass_pct": round(sum(c["assertions"]["all_pass"] for c in per_case.values()) / n * 100, 1),
        "by_assertion_pass_pct": by_assertion, "by_slice": slices,
        "taxonomy": taxonomy.score(taxo), "trajectory": trajectory.aggregate(traj_rows),
        "cost_per_claim_usd": {"mean": round(statistics.mean(costs), 6), "p50": round(statistics.median(costs), 6), "max": round(max(costs), 6)},
        "latency_ms": {"p50": round(statistics.median(lat), 1), "max": round(max(lat), 1)},
        "tokens_per_claim_mean": round(statistics.mean(r["tokens"] for r in records.values()), 1),
        "llm_calls_per_claim_mean": round(statistics.mean(r["iterations"] for r in records.values()), 2),
        "per_case": per_case,
    }


def judge_records(records):
    cases = policies.load_cases()
    pairs = {cid: (cases[cid], rec) for cid, rec in records.items()}
    verdicts = judge.run_judge_all(pairs)
    return {"verdicts": verdicts, "agreement": judge.agreement(verdicts)}
