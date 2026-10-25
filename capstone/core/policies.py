"""Claim, policy, in-force and referral data tools. Deterministic, no model, read-only except refer."""

import json
import threading

from capstone.core.dates import BadDate, parse_date, parse_end
from capstone.core.paths import DATA, REPORTS

_CACHE = {}
_REFERRAL_LOCK = threading.Lock()


def load_cases():
    if "cases" not in _CACHE:
        rows = [json.loads(l) for l in (DATA / "claims.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        _CACHE["cases"] = {r["id"]: r for r in rows}
    return _CACHE["cases"]


def register_case(case):
    """Add an ad-hoc FNOL (run.py triage --fnol ...) to the in-memory case table."""
    load_cases()[case["id"]] = case
    return case["id"]


def load_policies():
    if "policies" not in _CACHE:
        _CACHE["policies"] = json.loads((DATA / "policies.json").read_text(encoding="utf-8"))["policies"]
    return _CACHE["policies"]


def _err(code, message):
    return {"recoverable": True, "error": {"code": code, "message": message}}


def get_claim(claim_id):
    """The FNOL record as the agent sees it. The PII deny-list stays out of the record."""
    case = load_cases().get(str(claim_id))
    if case is None:
        return _err("CLAIM_NOT_FOUND", f"unknown claim id {claim_id!r}")
    return {k: case[k] for k in ("id", "claim_number", "product", "policy_number", "loss_date", "fnol")} | {
        "claim_id": case["id"]}


def lookup_policy(policy_number):
    """Policy schedule: product, period and the THREE separate money numbers."""
    pol = load_policies().get(str(policy_number).strip().upper())
    if pol is None:
        return _err("POLICY_NOT_FOUND", f"unknown policy number {policy_number!r}")
    return {"policy_number": str(policy_number).strip().upper(), "recoverable": False, **pol,
            "money_note": "deductible, each sub_limit and policy_limit are three different numbers; never merge them"}


def check_coverage_in_force(policy_number, loss_date):
    """Was this policy in force on the loss date? Returns a fact, never a coverage decision."""
    pol = load_policies().get(str(policy_number).strip().upper())
    if pol is None:
        return _err("POLICY_NOT_FOUND", f"unknown policy number {policy_number!r}")
    try:
        day = parse_date(loss_date)
    except BadDate as exc:
        return _err("BAD_DATE", str(exc))
    start, end = parse_date(pol["in_force_from"]), parse_end(pol["in_force_to"])
    ok = start <= day <= end
    why = ("loss date falls inside the policy period" if ok else
           "loss date is before the policy period began" if day < start else
           "loss date is after the policy period ended")
    return {"recoverable": False, "policy_number": str(policy_number).strip().upper(), "loss_date": day.isoformat(),
            "in_force": ok, "in_force_from": pol["in_force_from"], "in_force_to": pol["in_force_to"], "reason": why}


def refer_to_adjuster(claim_id, reason, citations=None):
    """Hand the claim to a human. This is the only terminal action; it decides nothing."""
    from capstone.core.pii import redact
    ticket = f"REF-{claim_id}"
    rec = {"referred": True, "ticket": ticket, "queue": "adjuster", "claim_id": str(claim_id),
           "reason": redact(str(reason or "")), "citations": citations or []}
    with _REFERRAL_LOCK:
        REPORTS.mkdir(parents=True, exist_ok=True)
        with open(REPORTS / "referrals.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"recoverable": False, **rec}
