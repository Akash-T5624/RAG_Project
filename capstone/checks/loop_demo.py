"""The closed failure -> test loop, demonstrated on the integrated system.

    python capstone/run.py loop [--trace <trace_id>]

1. take a failing trace (a wrong-endorsement-version run from suite_before.json, or the straddle cases when no live run exists)
2. turn it into a permanent eval case in data/guard_cases.jsonl (idempotent)
3. show it RED on the pre-fix behaviour (date filter off) and GREEN on the current code (filter on)
4. the case now runs in the unit-test suite forever (tests/test_capstone.py::GuardCases)
"""

import json

from capstone.core import policies, retriever
from capstone.core.dates import in_range, parse_date
from capstone.core.paths import DATA, REPORTS
from capstone.core.pii import redact

GUARD = DATA / "guard_cases.jsonl"


def load_guard_cases():
    if not GUARD.exists():
        return []
    return [json.loads(l) for l in GUARD.read_text(encoding="utf-8").splitlines() if l.strip()]


def check(gc, date_filter):
    """Version guard: pass iff no returned wording is out of force on the loss date. (hit@3 is reported separately.)"""
    case = policies.load_cases()[gc["case_id"]]
    res = retriever.search_wording(redact(case["fnol"]), case["product"], case["loss_date"], top_k=3, date_filter=date_filter)
    day = parse_date(case["loss_date"])
    out_of_force = [[h["clause_id"], h["endorsement_version"]] for h in res["hits"] if not in_range(day, h["effective_from"], h["effective_to"])]
    got = [[h["clause_id"], h["endorsement_version"]] for h in res["hits"]]
    return not out_of_force, {"out_of_force_hits": out_of_force, "returned": got, "expected_in_top3": gc["expect"] in got}


def _failing_trace(trace_id=None):
    p = REPORTS / "suite_before.json"
    if not p.exists():
        return None
    from capstone.checks.assertions import citation_version_in_force
    cases = policies.load_cases()
    for cid, rec in json.loads(p.read_text(encoding="utf-8")).items():
        if trace_id and rec["trace_id"] != trace_id:
            continue
        if not citation_version_in_force(cases[cid], rec)[0]:
            return cid, rec["trace_id"]
    return None


def main(trace_id=None, log=print):
    found = _failing_trace(trace_id)
    seeds = [(found[0], found[1])] if found else [(c["id"], None) for c in policies.load_cases().values() if "straddle" in c["slices"]]
    log("source of the failure: " + (f"trace {found[1]} (case {found[0]})" if found else
        "no live 'before' run found; seeding from the straddle cases (retrieval-level, no model)"))
    existing = {g["case_id"] for g in load_guard_cases()}
    with open(GUARD, "a", encoding="utf-8") as f:
        for cid, tid in seeds:
            if cid in existing:
                continue
            case = policies.load_cases()[cid]
            f.write(json.dumps({"case_id": cid, "from_trace": tid, "loss_date": case["loss_date"],
                                "expect": [case["expect"]["clause_id"], case["expect"]["endorsement_version"]],
                                "guards": "wording in force on the loss date, never a later endorsement"}) + "\n")
    rows = []
    for gc in load_guard_cases():
        red_ok, red = check(gc, date_filter=False)
        green_ok, green = check(gc, date_filter=True)
        rows.append((gc["case_id"], gc["loss_date"], "GREEN" if red_ok else "RED", "GREEN" if green_ok else "RED", red["out_of_force_hits"]))
        log(f"{gc['case_id']:7} loss {gc['loss_date']}  filter OFF: {rows[-1][2]:5}  filter ON: {rows[-1][3]:5}  out-of-force hits when off: {red['out_of_force_hits']}; expected clause in top-3 with filter on: {green['expected_in_top3']}")
    return rows


if __name__ == "__main__":
    main()
