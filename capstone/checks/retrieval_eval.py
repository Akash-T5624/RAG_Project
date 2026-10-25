"""Loss-date-aware retrieval, measured WITHOUT any model call (bonus challenge).

Query = the claim's FNOL text with claimant PII redacted. For every case with an expected
(clause, endorsement version) it runs retrieval twice on the same code path: date_filter=False
(best-ranked wording wins, any date: the pre-fix behaviour) and date_filter=True (enforced in code).

Metrics (n always stated):
  hit@3                 expected clause AT the expected version is in the top 3
  wrong_version_top3    any top-3 hit whose version was not in force on the loss date
  wrong_version_top1    the top-1 hit's version was not in force on the loss date
Reported separately for the straddle slice and for everything else (the non-straddle hit-rate cost).
"""

import json

from capstone.core import policies, retriever
from capstone.core.dates import in_range, parse_date
from capstone.core.pii import redact


def _slice_name(case):
    return "straddle" if "straddle" in case["slices"] else "non_straddle"


def run(top_k=3):
    rows = []
    for case in policies.load_cases().values():
        exp = case["expect"]
        if not exp.get("clause_id") or not case.get("loss_date"):
            continue
        day = parse_date(case["loss_date"])
        row = {"id": case["id"], "slice": _slice_name(case), "loss_date": case["loss_date"],
               "expected": [exp["clause_id"], exp["endorsement_version"]]}
        for mode, flt in (("before_no_filter", False), ("after_date_filter", True)):
            res = retriever.search_wording(redact(case["fnol"]), case["product"], case["loss_date"], top_k=top_k, date_filter=flt)
            hits = res["hits"]
            bad = [h for h in hits if not in_range(day, h["effective_from"], h["effective_to"])]
            row[mode] = {
                "ranker": res.get("ranker"),
                "returned": [[h["clause_id"], h["endorsement_version"]] for h in hits],
                "hit_at_k": [exp["clause_id"], exp["endorsement_version"]] in [[h["clause_id"], h["endorsement_version"]] for h in hits],
                "wrong_version_top_k": bool(bad),
                "wrong_version_top1": bool(hits) and hits[0] in bad,
            }
        rows.append(row)
    return rows


def summarize(rows):
    out = {}
    for sl in ("straddle", "non_straddle", "all"):
        sub = [r for r in rows if sl == "all" or r["slice"] == sl]
        out[sl] = {"n": len(sub)}
        for mode in ("before_no_filter", "after_date_filter"):
            n = len(sub) or 1
            out[sl][mode] = {m: round(sum(r[mode][k] for r in sub) / n * 100, 1) for m, k in
                             (("hit@3_pct", "hit_at_k"), ("wrong_version_in_top3_pct", "wrong_version_top_k"),
                              ("wrong_version_top1_pct", "wrong_version_top1"))}
    return out


if __name__ == "__main__":
    rows = run()
    print(json.dumps(summarize(rows), indent=1))
