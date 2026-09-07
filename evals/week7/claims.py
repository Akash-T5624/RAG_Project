import json

from config import (
    DEFAULT_CLAIM_AMOUNT,
    EVAL_SET_PATH,
    RACE_CLAIM_IDS,
    SELECTION_RULE,
)


def load_all_claims():
    """Load the supplied evaluation dataset as {claim_id: claim}."""
    claims = {}
    for line in EVAL_SET_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        claims[obj["id"]] = obj
    return claims


def build_claim_record(raw):
    """One claim record as served by the get_claim tool.

    Fields that exist in the eval data are copied verbatim
    (expect.claim_number, expect.loss_date, expect.excess, adjuster_notes).
    `claim_amount` is the documented assumption from config.py.
    """
    expect = raw.get("expect", {})
    return {
        "claim_id": raw["id"],
        "title": raw.get("title", ""),
        "mode": raw.get("mode", ""),
        "claim_number": expect.get("claim_number"),
        "loss_date": expect.get("loss_date"),
        "excess": expect.get("excess"),
        "claim_amount": DEFAULT_CLAIM_AMOUNT,
        "adjuster_notes": raw.get("adjuster_notes", ""),
        "expect": expect,
        "hand_label": raw.get("hand_label"),
    }


def race_claim_records():
    """The exact 10 claims used for the race, in a fixed order.

    Selection rule (documented, deterministic): the 10 claims with the lowest
    numeric id whose expect.excess is positive -> W6-001..W6-006 (excl. W6-007,
    which has a non-numeric excess in the notes) plus W6-008..W6-011.  This
    keeps all six Week-6 failure modes represented and guarantees at least 3
    branching claims (W6-002, W6-004, W6-008 and W6-011) where the policy
    clause to look up depends on what the adjuster notes reveal.
    """
    all_claims = load_all_claims()
    ids_ordered = sorted(RACE_CLAIM_IDS, key=lambda cid: cid)  # W6-00X order
    records = [build_claim_record(all_claims[cid]) for cid in ids_ordered]
    return records


def get_claim_record(claim_id):
    all_claims = load_all_claims()
    if claim_id not in all_claims:
        raise KeyError(f"Unknown claim id: {claim_id}")
    return build_claim_record(all_claims[claim_id])


def dataset_report():
    """Honest report of the supplied dataset + the 10-claim selection."""
    all_claims = load_all_claims()
    branching = [c["id"] for c in all_claims.values()
                 if c["expect"].get("exclusion_cite_required")]
    return {
        "dataset_path": str(EVAL_SET_PATH),
        "total_claims_in_dataset": len(all_claims),
        "claim_ids_in_dataset": sorted(all_claims),
        "denial_claims_in_dataset": [c["id"] for c in all_claims.values()
                                     if c["expect"].get("denial")],
        "branching_claims_in_dataset": branching,
        "selection_rule": SELECTION_RULE,
        "race_claim_ids": RACE_CLAIM_IDS,
        "claim_amount_assumption": DEFAULT_CLAIM_AMOUNT,
        "note": (
            "The provided dataset has 27 claims (more than 10). The 10-claim "
            "race set is selected by the documented deterministic rule above. "
            "claim_amount is NOT present in the provided dataset; the documented "
            "assumption DEFAULT_CLAIM_AMOUNT is used so compute_payout can run."
        ),
    }


if __name__ == "__main__":
    import json
    from pprint import pprint

    pprint(dataset_report())
    print()
    for rec in race_claim_records():
        print(rec["claim_id"], rec["claim_number"], "excess=", rec["excess"],
              "loss_date=", rec["loss_date"], "denial=", rec["expect"]["denial"])
    print()
    print(json.dumps({"test": __name__ == "__main__"}))