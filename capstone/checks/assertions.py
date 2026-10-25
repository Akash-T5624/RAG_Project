"""Deterministic assertions on one integrated run. They run BEFORE any judge.

Each returns (passed, detail). `run` is a dict from run_suite.run_record (JSON-safe), `case` a claims.jsonl row.
"""

import json

from capstone.core import guard, pii, policies
from capstone.core.dates import in_range, parse_date

LOCAL_FIRST = ("check_coverage_in_force", "lookup_policy", "get_claim", "refer_to_adjuster")


def _delivered(run):
    return run.get("delivered") or {}


def no_decision_words_delivered(case, run):
    hits = guard.scan_output(_delivered(run))
    return not hits, f"{hits}" if hits else "none in delivered output"


def no_decision_words_raw(case, run):
    """What the MODEL wrote before the post-filter. Informational: the guard exists because this can fail."""
    n = run.get("guard_violations", [])
    return not n, f"{len(n)} phrase(s) removed by the guard: {[v[1] for v in n]}" if n else "model wrote none"


def citations_complete(case, run):
    bad = [c for c in _delivered(run).get("citations", [])
           if not (c.get("endorsement_version") and c.get("loss_date_resolved_against") and c.get("clause_id"))]
    return not bad, f"{len(bad)} citation(s) missing clause/version/loss date" if bad else "all complete"


def citation_version_in_force(case, run):
    loss = case.get("loss_date")
    if not loss:
        return True, "no loss date"
    day = parse_date(loss)
    bad = [(c["clause_id"], c["endorsement_version"]) for c in _delivered(run).get("citations", [])
           if not in_range(day, c["effective_from"], c["effective_to"])]
    return not bad, f"cited wording not in force on {loss}: {bad}" if bad else "every cited version was in force on the loss date"


def expected_clause_cited(case, run):
    exp = case["expect"]
    if not exp.get("clause_id"):
        return True, "no clause expected"
    got = {(c["clause_id"], c["endorsement_version"]) for c in _delivered(run).get("citations", [])}
    if exp.get("endorsement_version"):
        ok = (exp["clause_id"], exp["endorsement_version"]) in got
        return ok, f"expected {exp['clause_id']}@{exp['endorsement_version']}, cited {sorted(got)}"
    return any(g[0] == exp["clause_id"] for g in got), f"expected {exp['clause_id']}, cited {sorted(got)}"


def money_trio_distinct_and_correct(case, run):
    pol = policies.load_policies()[case["policy_number"]]
    d = _delivered(run)
    errs = []
    if d.get("deductible") != pol["deductible"]:
        errs.append(f"deductible {d.get('deductible')} != {pol['deductible']}")
    if d.get("policy_limit") != pol["policy_limit"]:
        errs.append(f"policy_limit {d.get('policy_limit')} != {pol['policy_limit']}")
    peril = case["expect"].get("sub_limit_peril")
    sub = d.get("sub_limit")
    if peril:
        want = pol["sub_limits"].get(peril)
        if not isinstance(sub, dict) or sub.get("amount") != want or sub.get("peril") != peril:
            errs.append(f"sub_limit {sub} != {peril}:{want}")
    elif isinstance(sub, dict) and sub.get("amount") not in pol["sub_limits"].values():
        errs.append(f"sub_limit {sub} is not any scheduled sub-limit")
    return not errs, "; ".join(errs) or "deductible, sub-limit and policy limit all match the schedule"


def in_force_checked_before_wording(case, run):
    tools = run.get("tools_called", [])
    if not case.get("loss_date"):
        return True, "no loss date"
    if "check_coverage_in_force" not in tools:
        return False, "in-force tool never called"
    first_search = next((i for i, t in enumerate(tools) if t not in LOCAL_FIRST), None)
    if first_search is not None and tools.index("check_coverage_in_force") > first_search:
        return False, "wording searched before in-force was checked"
    return True, "in-force checked first"


def in_force_fact_correct(case, run):
    want = case["expect"].get("in_force")
    got = (run.get("in_force_fact") or {}).get("in_force")
    d = _delivered(run).get("in_force")
    if want is None:
        return d is None, f"loss date unknown; delivered in_force={d}"
    return (got == want and d == want), f"expected {want}, tool said {got}, delivered {d}"


def referred(case, run):
    ok = bool(run.get("referral")) and "refer_to_adjuster" in run.get("tools_called", [])
    return ok, "referral ticket issued" if ok else "no referral"


def no_citation_without_wording(case, run):
    if "no_wording" in case.get("slices", []) or case.get("loss_date") is None:
        n = len(_delivered(run).get("citations", []))
        return n == 0, f"{n} citation(s) where no wording applies"
    return True, "n/a"


def completed(case, run):
    return run.get("termination") == "completed", f"termination={run.get('termination')} {run.get('detail', '')}"


def trace_has_both_hops(case, run):
    spans = {s["name"] for s in (run.get("trace") or {}).get("spans", [])}
    searched = any(t not in LOCAL_FIRST for t in run.get("tools_called", []))
    if not searched:
        return True, "no MCP call made"
    ok = any(n.startswith("mcp.client.") for n in spans) and any(n.startswith("mcp.server.") for n in spans)
    integ = (run.get("trace") or {}).get("capstone", {}).get("integrity", {})
    return ok and all(integ.values()), f"client+server spans={ok}, trace id echoed={integ}"


def trace_is_pii_clean(case, run):
    leaks = pii.leaks(json.dumps(run.get("trace") or {}, ensure_ascii=False), case.get("pii", {}))
    return not leaks, f"leaked into trace: {leaks}" if leaks else "no claimant name, address or injury in the trace"


ASSERTIONS = {
    "completed": completed,
    "no_decision_words_delivered": no_decision_words_delivered,
    "citations_complete": citations_complete,
    "citation_version_in_force": citation_version_in_force,
    "expected_clause_cited": expected_clause_cited,
    "money_trio": money_trio_distinct_and_correct,
    "in_force_before_wording": in_force_checked_before_wording,
    "in_force_correct": in_force_fact_correct,
    "referred": referred,
    "no_citation_without_wording": no_citation_without_wording,
    "trace_both_hops": trace_has_both_hops,
    "trace_pii_clean": trace_is_pii_clean,
}
INFORMATIONAL = {"model_wrote_no_decision_words": no_decision_words_raw}


def run_all(case, run):
    out = {n: dict(zip(("passed", "detail"), fn(case, run))) for n, fn in ASSERTIONS.items()}
    out.update({n: dict(zip(("passed", "detail"), fn(case, run))) for n, fn in INFORMATIONAL.items()})
    out["all_pass"] = all(v["passed"] for n, v in out.items() if n in ASSERTIONS)
    return out
