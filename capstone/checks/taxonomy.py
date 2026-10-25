"""Week 5 error taxonomy, re-scored on the integrated system.

Modes are assigned by rule from assertion results and run flags, never by hand. Only modes that were
observed are reported (frequency %, severity, one trace id each). `created_by_integration` marks modes
that exist only because the parts were joined (they could not occur in any single week's system).
"""

from capstone.core.dates import in_range, parse_date

# mode -> (severity, created_by_integration, description)
MODES = {
    "wrong-endorsement-version": ("critical", False,
                                  "cited wording whose endorsement version was not in force on the loss date (the 2024-loss / 2025-wording failure)"),
    "decision-language": ("critical", False, "model wrote a coverage/approval/payment decision; the guard had to remove it"),
    "money-conflation": ("high", False, "deductible, sub-limit or policy limit wrong, swapped or merged"),
    "expected-wording-not-cited": ("high", False, "the clause the adjuster needs was not cited (retrieval miss or model skipped it)"),
    "mcp-arg-drift": ("medium", True, "model sent the MCP tool a loss date or product that differs from the claim record; code had to enforce the real one"),
    "unretrieved-citation": ("high", True, "model cited a clause/version that no retrieval hop returned"),
    "trace-id-lost-at-hop": ("high", True, "trace id was not echoed by the MCP server, breaking end-to-end correlation"),
    "pii-in-trace": ("critical", True, "claimant name, address or injury text reached the persisted trace"),
    "process-skipped": ("medium", False, "required step missing or run did not complete (in-force check, referral, budget, parse)"),
}


def classify(case, run, assertions):
    """Return the list of mode names this run exhibits."""
    a = assertions
    modes = []
    if not a["citation_version_in_force"]["passed"]:
        modes.append("wrong-endorsement-version")
    if run.get("guard_violations"):
        modes.append("decision-language")
    if not a["money_trio"]["passed"]:
        modes.append("money-conflation")
    if not a["expected_clause_cited"]["passed"] and a["citation_version_in_force"]["passed"]:
        modes.append("expected-wording-not-cited")
    if run.get("args_overridden"):
        modes.append("mcp-arg-drift")
    if run.get("unretrieved_citations"):
        modes.append("unretrieved-citation")
    if not a["trace_both_hops"]["passed"]:
        modes.append("trace-id-lost-at-hop")
    if not a["trace_pii_clean"]["passed"]:
        modes.append("pii-in-trace")
    if not (a["completed"]["passed"] and a["in_force_before_wording"]["passed"] and a["referred"]["passed"]):
        modes.append("process-skipped")
    return modes


def score(per_case):
    """per_case: [{id, modes, trace_id}] -> taxonomy table rows."""
    n = len(per_case) or 1
    rows = []
    for mode, (sev, integ, desc) in MODES.items():
        hit = [c for c in per_case if mode in c["modes"]]
        if hit:
            rows.append({"mode": mode, "n": len(hit), "frequency_pct": round(len(hit) / n * 100, 1), "severity": sev,
                         "created_by_integration": integ, "example_trace_id": hit[0]["trace_id"],
                         "example_case": hit[0]["id"], "description": desc})
    sev_rank = {"critical": 0, "high": 1, "medium": 2}
    rows.sort(key=lambda r: (sev_rank[r["severity"]], -r["n"]))
    return rows
