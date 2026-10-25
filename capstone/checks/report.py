"""Generate reports/EVAL_REPORT.md and reports/COST_10X.md from MEASURED results only.

Anything not measured is printed as NOT RUN / NOT MEASURED. No number in the output is typed by hand.
"""

import json
import statistics

from capstone.checks import retrieval_eval, run_suite
from capstone.core import policies
from capstone.core.paths import REPORTS

NON_COVERAGE = [
    "Real policy wording: the endorsement sets, policies and limits are SYNTHETIC fixtures authored for this capstone.",
    "Scale: n is the case count shown, not the 500 claim files of the brief; no statistical confidence is claimed.",
    "Parse quality: no PDF exclusions schedule is parsed or measured; the wording store is authored JSON.",
    "Bare names: redaction is pattern-based; a claimant name with no title or label is not caught.",
    "Multi-claim and multi-policy claims, endorsements issued mid-term, and policy period vs wording-version mismatch.",
    "Reproducing a model answer 18 months later: retrieval is replayable from the trace, the model's wording is not guaranteed.",
    "Live load: throughput is extrapolated from sequential runs, not load-tested.",
]


def _load(mode):
    p = run_suite.path_for(mode)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def wrong_version_rate(records):
    """Share of runs that cited anything and cited at least one wording not in force on the loss date."""
    cases = policies.load_cases()
    from capstone.checks.assertions import citation_version_in_force
    cited = [(cid, r) for cid, r in records.items() if (r.get("delivered") or {}).get("citations")]
    wrong = [cid for cid, r in cited if not citation_version_in_force(cases[cid], r)[0]]
    return {"n_with_citations": len(cited), "n_wrong": len(wrong), "pct": round(len(wrong) / len(cited) * 100, 1) if cited else None,
            "wrong_cases": wrong}


def _table(rows, head):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]


def build_eval_report():
    L = ["# Integrated eval report", "", "All numbers below were produced by `python capstone/run.py eval` and "
         "`checks/retrieval_eval.py`; nothing is hand-typed. Data is SYNTHETIC (see non-coverage).", ""]
    rows = retrieval_eval.run()
    s = retrieval_eval.summarize(rows)
    rk = rows[0]["after_date_filter"]["ranker"] if rows else "?"
    L += [f"## 1. Loss-date-aware retrieval, no model involved (ranker: `{rk}`)", "",
          "Query = FNOL text (PII redacted). Same code path, filter off vs on.", ""]
    L += _table([[sl, s[sl]["n"], m, *(s[sl][m][k] for k in ("hit@3_pct", "wrong_version_in_top3_pct", "wrong_version_top1_pct"))]
                 for sl in ("straddle", "non_straddle", "all") for m in ("before_no_filter", "after_date_filter")],
                ["slice", "n", "mode", "hit@3 %", "wrong version in top-3 %", "wrong version top-1 %"])
    L += ["", "Per-case misses after the filter: " + (", ".join(f"{r['id']} (returned {r['after_date_filter']['returned']}, expected {r['expected']})"
                                                         for r in rows if not r["after_date_filter"]["hit_at_k"]) or "none"), ""]

    after, before = _load("after"), _load("before")
    ev_after = run_suite.evaluate(after) if after else None
    ev_before = run_suite.evaluate(before) if before else None
    L += ["## 2. Wrong-endorsement-version citation rate (the number that moved)", ""]
    if after and before:
        wb, wa = wrong_version_rate(before), wrong_version_rate(after)
        L += _table([["before (filter off)", wb["n_with_citations"], wb["n_wrong"], wb["pct"], ", ".join(wb["wrong_cases"])],
                     ["after (filter on)", wa["n_with_citations"], wa["n_wrong"], wa["pct"], ", ".join(wa["wrong_cases"])]],
                    ["run", "runs that cited", "wrong version", "rate %", "cases"])
    else:
        L += ["NOT RUN: needs both `run.py eval --before` and `run.py eval` (live model + GROQ_API_KEY)."]
    for name, ev in (("after (date filter ON)", ev_after), ("before (date filter OFF)", ev_before)):
        L += ["", f"## 3. Integrated suite, {name}", ""]
        if not ev:
            L += ["NOT RUN."]
            continue
        L += [f"n = {ev['n']} cases; all-assertions pass = {ev['overall_pass_pct']}% (an average: read the slices).", ""]
        L += _table([[k, v] for k, v in ev["by_assertion_pass_pct"].items()], ["assertion", "pass %"])
        L += ["", "Per slice (the exclusion / straddle numbers matter, not the average):", ""]
        L += _table([[k, v["n"], v["all_assertions_pass_pct"], v["citation_version_in_force_pct"]] for k, v in ev["by_slice"].items()],
                    ["slice", "n", "all assertions %", "cited version in force %"])
        L += ["", "### Error taxonomy re-scored", ""]
        L += _table([[t["mode"], t["n"], t["frequency_pct"], t["severity"], "YES" if t["created_by_integration"] else "", t["example_trace_id"]]
                     for t in ev["taxonomy"]], ["mode", "n", "freq %", "severity", "created by integration", "trace id"]) if ev["taxonomy"] else ["No failure mode observed in this run."]
        L += ["", "### Trajectory", "", "```json", json.dumps(ev["trajectory"], indent=1), "```"]
    jf = REPORTS / "judge_after.json"
    L += ["", "## 4. Judge agreement on integrated outputs", ""]
    if jf.exists():
        j = json.loads(jf.read_text(encoding="utf-8"))["agreement"]
        L += [("Agreement with hand labels: " + f"{j['agree']}/{j['n']} = {j['pct']}% (bar 85%: {'CLEARED' if j['clears_85'] else 'NOT CLEARED'})")
              if j.get("measured") else f"NOT MEASURED: {j.get('reason')}"]
    else:
        L += ["NOT RUN: `run.py judge` (needs the suite run and your hand labels)."]
    L += ["", "## 5. What this suite does NOT cover", ""] + [f"- {x}" for x in NON_COVERAGE]
    (REPORTS / "EVAL_REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return REPORTS / "EVAL_REPORT.md"


def build_cost_plan(weekly_fnols=18000, peak_factor=5.0, tpm_limit=None):
    """Cost per claim and the 10x plan. Throughput limits come from measured runs; the provider TPM is an input."""
    out = REPORTS / "COST_10X.md"
    recs = _load("after")
    if not recs:
        out.write_text("# Cost and 10x plan\n\nNOT MEASURED: run `python capstone/run.py eval` first.\n", encoding="utf-8")
        return out
    ev = run_suite.evaluate(recs)
    tok = ev["tokens_per_claim_mean"]
    p50_s = ev["latency_ms"]["p50"] / 1000.0
    cost = ev["cost_per_claim_usd"]["mean"]
    srv = [s["duration_ms"] for r in recs.values() for s in r["trace"]["spans"] if s["name"].startswith("mcp.server.")]
    srv_p50 = statistics.median(srv) if srv else None
    week_s = 7 * 24 * 3600
    avg_rate = weekly_fnols / week_s
    peak_rate = avg_rate * peak_factor
    need_workers = peak_rate * p50_s
    need_tpm = peak_rate * 60 * tok
    L = ["# Cost per claim and the 10x plan", "", f"Measured on n = {ev['n']} integrated runs (model `{next(iter(recs.values()))['model']}`).", "",
         f"- Cost per claim triaged: mean ${ev['cost_per_claim_usd']['mean']}, p50 ${ev['cost_per_claim_usd']['p50']}, max ${ev['cost_per_claim_usd']['max']}",
         f"- Tokens per claim (mean): {tok}; model calls per claim (mean): {ev['llm_calls_per_claim_mean']}",
         f"- End-to-end latency p50: {p50_s:.1f}s (max {ev['latency_ms']['max'] / 1000:.1f}s); MCP server span p50: "
         f"{'%.0f ms' % srv_p50 if srv_p50 is not None else 'n/a'}", "",
         f"## Hailstorm week: {weekly_fnols:,} FNOLs", "",
         f"- Average arrival {avg_rate:.3f}/s; assumed peak factor x{peak_factor} (ASSUMPTION, not measured) = {peak_rate:.3f}/s",
         f"- Spend for the week: {weekly_fnols:,} x ${cost} = ${weekly_fnols * cost:,.2f}",
         f"- Concurrency needed at peak = peak rate x p50 latency = {peak_rate:.3f} x {p50_s:.1f}s = {need_workers:.1f} concurrent agent runs",
         f"- Tokens/minute needed at peak = {peak_rate:.3f}/s x 60 x {tok} = {need_tpm:,.0f} TPM"]
    if tpm_limit:
        L += [f"- Provider limit given: {tpm_limit:,} TPM -> headroom x{tpm_limit / need_tpm:.2f}" + ("  **BREAKS FIRST**" if tpm_limit < need_tpm else "")]
    else:
        L += ["- Provider TPM limit: NOT PROVIDED. Re-run `python capstone/run.py cost --tpm <your Groq org limit>` to name the binding limit."]
    L += ["", "What breaks first is whichever of these has the smallest headroom: provider TPM (above), concurrent runs (this repo has no worker pool: "
          "the loop is sequential per process and the Week 7 client sleeps on 429), or the single MCP server process "
          "(one cross-encoder, one process; measure it under concurrency before trusting the span p50).", ""]
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return out
