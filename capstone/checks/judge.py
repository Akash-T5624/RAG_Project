"""LLM judge on integrated outputs, re-using the Week 6 judge machinery (retries, parsing).

The Week 6 prompt (judge_v2) asks "acceptable to route for final approval?" and rewards an "Approve"
recommendation. That criterion is wrong for a system that must never decide, so this module uses
the same run_judge() with a new prompt, judge_integrated_v1.txt, and its agreement with HAND labels
must be measured again. Labels are NEVER auto-generated: `run.py label-sheet` writes the sheet and
you fill capstone/data/hand_labels.json. Without labels the agreement is reported as NOT MEASURED.
"""

import importlib.util
import json

from capstone.core.paths import DATA, REPORTS, WEEK6
from capstone.core.pii import redact

PROMPT = DATA / "judge_integrated_v1.txt"
LABELS = DATA / "hand_labels.json"


def _w6():
    spec = importlib.util.spec_from_file_location("w6_judge", WEEK6 / "judge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def briefing(delivered):
    """The adjuster-facing text for one run: this is what the judge and the human labeller read."""
    d = delivered or {}
    lines = [f"Claim {d.get('claim_id')} | policy {d.get('policy_number')} | loss date {d.get('loss_date')}",
             f"Policy in force on loss date (tool fact): {(d.get('in_force_checked') or {}).get('in_force')}"]
    for c in d.get("citations", []):
        lines.append(f"Wording [{c['clause_id']}, endorsement {c['endorsement_version']}, in force {c['effective_from']}"
                     f" to {c['effective_to'] or 'open'}, resolved against loss date {c['loss_date_resolved_against']}]: \"{c['quote']}\"")
    if not d.get("citations"):
        lines.append("Wording: none cited")
    sub = d.get("sub_limit") or {}
    lines.append(f"Deductible: {d.get('deductible')} | Sub-limit ({sub.get('peril')}): {sub.get('amount')} | Policy limit: {d.get('policy_limit')}")
    lines += [f"Fact: {f}" for f in d.get("facts_for_adjuster", [])]
    lines.append(f"Referred to adjuster: {d.get('referral_reason')}")
    return "\n".join(lines)


def run_judge_all(cases_runs):
    """cases_runs: {id: (case, run)} -> {id: verdict}. Costs one Groq call per case."""
    w6 = _w6()
    template = PROMPT.read_text(encoding="utf-8")
    out = {}
    for cid, (case, run) in cases_runs.items():
        try:
            verdict, raw = w6.run_judge(template, redact(case["fnol"]), briefing(run.get("delivered")))
        except Exception as exc:  # noqa: BLE001
            verdict, raw = "ERROR", str(exc)[:200]
        out[cid] = {"verdict": verdict, "raw": raw}
    return out


def agreement(verdicts):
    """Agreement with hand labels ('acceptable'/'reject'). Returns None-valued dict when no labels exist."""
    if not LABELS.exists():
        return {"measured": False, "reason": f"{LABELS.name} does not exist: no hand labels yet"}
    labels = {k: v for k, v in json.loads(LABELS.read_text(encoding="utf-8")).items() if v in ("acceptable", "reject")}
    if not labels:
        return {"measured": False, "reason": "hand_labels.json has no filled labels"}
    rows, agree = {}, 0
    for cid, human in labels.items():
        v = (verdicts.get(cid) or {}).get("verdict")
        judge = {"ACCEPT": "acceptable", "REJECT": "reject"}.get(v)
        ok = judge == human
        agree += ok
        rows[cid] = {"human": human, "judge": judge or v, "match": ok}
    pct = round(agree / len(labels) * 100, 1)
    return {"measured": True, "n": len(labels), "agree": agree, "pct": pct, "clears_85": pct >= 85.0, "details": rows}


def label_sheet(cases_runs, path=None):
    path = path or (REPORTS / "labeling_sheet.md")
    lines = ["# Hand-labelling sheet", "",
             "Criterion: is this adjuster briefing SAFE to route? It must be faithful to the FNOL and the tool facts, invent nothing,",
             "cite wording whose version was in force on the loss date, keep deductible / sub-limit / policy limit apart, and NOT state a decision.",
             "Write `acceptable` or `reject` per id in capstone/data/hand_labels.json BEFORE running the judge.", ""]
    for cid, (case, run) in cases_runs.items():
        lines += [f"## {cid}", "", f"FNOL (redacted): {redact(case['fnol'])}", "", "```", briefing(run.get("delivered")), "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
