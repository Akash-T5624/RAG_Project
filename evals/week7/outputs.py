import re

OUTPUT_FIELDS = [
    "claim_id",
    "claim_number",
    "loss_date",
    "status",
    "excess",
    "exclusion_clause",
    "payout",
]

EXC_RE = re.compile(r"\bExc[- ]?\d+\.\d+", re.IGNORECASE)


def expected_output(claim_record):
    """Derive the expected output strictly from the provided eval data."""
    expect = claim_record["expect"]
    status_by_denial = (
        "excluded" if expect.get("exclusion_cite_required")
        else "rejected"
    )
    expected_status = (
        "approved"
        if not expect.get("denial", False)
        else status_by_denial
    )
    notes = claim_record.get("adjuster_notes", "")
    clause = None
    if expect.get("exclusion_cite_required"):
        m = EXC_RE.search(notes)
        clause = m.group(0).replace(" ", "") if m else None

    excess = expect.get("excess")
    claim_amount = claim_record["claim_amount"]
    if expected_status == "approved" and excess is not None:
        payout = max(0.0, float(claim_amount) - float(excess))
    else:
        payout = 0.0

    return {
        "claim_id": claim_record["claim_id"],
        "claim_number": expect.get("claim_number"),
        "loss_date": expect.get("loss_date"),
        "status": expected_status,
        "excess": excess,
        "exclusion_clause": clause,
        "payout": round(float(payout), 2),
    }


def _norm_clause(value):
    if value is None:
        return None
    text = str(value).strip().replace(" ", "").upper()
    return text or None


def _norm_date(value):
    if value is None:
        return None
    return str(value).strip()


def validate_output(output, expected):
    """Validate an output dict against the expected contract.

    Returns (all_pass: bool, failures: list[str]).
    A response only PASSES when every applicable field matches.  Leniency is
    limited and documented: when the dataset labels a loss date as unknown
    (expected null), any loss_date is accepted as benign (there is no hidden
    truth to check).
    """
    failures = []

    if output is None:
        return False, ["no output produced"]
    if not isinstance(output, dict):
        return False, [f"output is not an object: {type(output)!r}"]

    missing = [f for f in OUTPUT_FIELDS if f not in output]
    if missing:
        failures.append(f"missing fields: {missing}")

    claim_id = output.get("claim_id")
    if str(claim_id) != str(expected["claim_id"]):
        failures.append(
            f"claim_id: expected {expected['claim_id']!r}, got {claim_id!r}"
        )

    claim_number = output.get("claim_number")
    if expected["claim_number"] is not None and (
        str(claim_number).strip().upper()
        != str(expected["claim_number"]).strip().upper()
    ):
        failures.append(
            f"claim_number: expected {expected['claim_number']!r}, "
            f"got {claim_number!r}"
        )

    # loss_date: exact when the data labels one; benign when unknown.
    if expected["loss_date"] is not None:
        if _norm_date(output.get("loss_date")) != expected["loss_date"]:
            failures.append(
                f"loss_date: expected {expected['loss_date']!r}, "
                f"got {output.get('loss_date')!r}"
            )

    status = output.get("status")
    if status != expected["status"]:
        failures.append(
            f"status: expected {expected['status']!r}, got {status!r}"
        )

    excess = output.get("excess")
    if expected["excess"] is not None:
        if excess is None:
            failures.append(f"excess: expected {expected['excess']!r}, got null")
        else:
            try:
                if abs(float(excess) - float(expected["excess"])) > 0.01:
                    failures.append(
                        f"excess: expected {expected['excess']!r}, got {excess!r}"
                    )
            except (TypeError, ValueError):
                failures.append(f"excess: not numeric: {excess!r}")

    # exclusion_clause: must match when one is required, must be absent otherwise.
    exp_clause = _norm_clause(expected["exclusion_clause"])
    got_clause = _norm_clause(output.get("exclusion_clause"))
    if exp_clause is not None:
        if got_clause != exp_clause:
            failures.append(
                f"exclusion_clause: expected {exp_clause!r}, got {got_clause!r}"
            )
    else:
        if got_clause is not None:
            failures.append(
                f"exclusion_clause: not expected but got {got_clause!r}"
            )

    payout = output.get("payout")
    try:
        if abs(float(payout) - float(expected["payout"])) > 0.01:
            failures.append(
                f"payout: expected {expected['payout']!r}, got {payout!r}"
            )
    except (TypeError, ValueError):
        failures.append(f"payout: not numeric: {payout!r}")

    return len(failures) == 0, failures