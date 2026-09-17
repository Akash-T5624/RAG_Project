"""Trajectory metrics: the four required numbers plus the gap.

Tool-choice accuracy     how many tool-call slots were the right tool at the
                         right position (correct steps / total steps)
Argument validity rate   how many LLM-supplied argument/final slots carried
                         *real* data (claim number, amounts, excess, clause id,
                         query relevance) instead of fluent fiction
Step efficiency          steps taken / steps needed (2 clean, 3 exclusion-cite)
Cost per claim           p50 and max, plus mean/min for context — never a bare
                         mean
"""

import re
from statistics import median

from expected_sequences import expected_spec, validate_sequence
from taxonomy import MODE_GROUPS, MODES, classify, PolicyCorpus, clause_provenance


def _content_tokens(text):
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def _norm(value):
    if value is None:
        return None
    return str(value).strip().upper()


def claim_metrics(claim_id, record, run, corpus):
    """Per-claim trajectory metrics; returns a dict + list of flagged modes."""
    steps = getattr(run, "steps", None) or []
    output = getattr(run, "output", None)
    spec = expected_spec(claim_id)
    allowed = (set(spec["required_tools"]) | set(spec["optional_tools"])
               | set(spec["policy_group"]))

    # ---- tool-choice accuracy -------------------------------------------
    middle_tools = set(spec["policy_group"]) | {"validate_claim_number"}
    tool_names = [s.get("tool") for s in steps]
    n = len(tool_names)
    correct = 0
    for i, t in enumerate(tool_names):
        ok = t in allowed
        if ok and t == "get_claim":
            ok = (i == 0)                      # get_claim must open the run
        if ok and t == "compute_payout":
            ok = (i == n - 1)                  # payout is the last tool call
        if ok and t in middle_tools:
            ok = (i not in (0, n - 1))         # middle, after claim before payout
        if ok:
            correct += 1
    tool_choice_accuracy = (correct / n) if n else 0.0

    # ---- argument validity rate ------------------------------------------
    slots = []  # (label, valid_bool)

    for s in steps:
        t = s.get("tool")
        args = s.get("args") or {}
        if t == "get_claim":
            slots.append(("claim_id", str(args.get("claim_id")) == str(claim_id)))
        elif t == "search_policy":
            query = args.get("query") or ""
            overlap = _content_tokens(query) & _content_tokens(
                record.get("adjuster_notes", ""))
            slots.append(("query_relevance", bool(query.strip()) and bool(overlap)))
        elif t == "retrieve_policy_exclusions":
            topic = args.get("topic") or ""
            overlap = _content_tokens(topic) & _content_tokens(
                record.get("adjuster_notes", ""))
            slots.append(("topic_relevance", bool(topic.strip()) and bool(overlap)))
        elif t == "validate_claim_number":
            got = str(args.get("claim_number") or "").strip().upper()
            want = str(record.get("claim_number") or "").strip().upper()
            slots.append(("claim_number_matches_record", bool(want) and got == want))
        elif t == "escalate_claim":
            cid_ok = str(args.get("claim_id")) == str(claim_id)
            reason_ok = bool(str(args.get("reason") or "").strip())
            slots.append(("escalate_claim_id", cid_ok))
            slots.append(("escalate_reason", reason_ok))
        elif t == "compute_payout":
            rec_amount, rec_excess = record.get("claim_amount"), record.get("excess")
            try:
                amount_ok = (rec_amount is not None) and abs(
                    float(args.get("claim_amount")) - float(rec_amount)) <= 0.01
            except (TypeError, ValueError):
                amount_ok = False
            try:
                excess_ok = (rec_excess is not None) and abs(
                    float(args.get("excess")) - float(rec_excess)) <= 0.01
            except (TypeError, ValueError):
                excess_ok = False
            slots.append(("claim_amount", amount_ok))
            slots.append(("excess", excess_ok))

    if output:
        rec_num = record.get("claim_number")
        slots.append((
            "final_claim_number",
            rec_num is not None and _norm(output.get("claim_number")) == _norm(rec_num),
        ))
        clause = output.get("exclusion_clause")
        if clause is not None:
            slots.append(("final_clause_real",
                          clause_provenance(record, clause, corpus)))
        else:
            slots.append(("final_clause_real", True))
        status = output.get("status")
        slots.append(("final_status_in_enum", status in ("approved", "excluded", "rejected")))

    checked = m_valid = 0
    invalid_labels = []
    for label, ok in slots:
        checked += 1
        if ok:
            m_valid += 1
        else:
            invalid_labels.append(label)
    argument_validity = (m_valid / checked) if checked else 1.0

    # ---- step efficiency --------------------------------------------------
    needed = spec["min_steps"]
    taken = n
    step_efficiency = taken / needed if needed else 0.0

    # ---- trajectory pass --------------------------------------------------
    seq_ok, seq_reason, accepted = validate_sequence(claim_id, steps)
    modes = classify(record, run, corpus)
    modes = set(modes)

    return {
        "claim_id": claim_id,
        "title": record.get("title", ""),
        "branching": spec["branching"],
        "steps": tool_names,
        "steps_taken": taken,
        "steps_needed": needed,
        "step_efficiency": round(step_efficiency, 3),
        "tool_choice_accuracy": round(tool_choice_accuracy, 4),
        "argument_validity": round(argument_validity, 4),
        "invalid_argument_slots": invalid_labels,
        "cost": float(getattr(run, "cost", 0.0)),
        "latency_ms": float(getattr(run, "latency_ms", 0.0)),
        "tokens": int(getattr(run, "tokens", 0)),
        "outcome_pass": bool(getattr(run, "pass_result", False)),
        "outcome_failures": list(getattr(run, "failures", []) or []),
        "sequence_pass": seq_ok,
        "sequence_reason": seq_reason,
        "accepted_path": accepted,
        "modes": sorted(modes),
        "trajectory_pass": seq_ok and not modes,
        "termination_reason": getattr(run, "termination_reason", "completed"),
    }


def aggregate(claim_metrics_list):
    """Roll per-claim metrics into the four required trajectory numbers."""
    n = len(claim_metrics_list) or 1
    outcome_pass = sum(1 for m in claim_metrics_list if m["outcome_pass"])
    trajectory_pass = sum(1 for m in claim_metrics_list if m["trajectory_pass"])
    seq_pass = sum(1 for m in claim_metrics_list if m["sequence_pass"])
    costs = sorted(m["cost"] for m in claim_metrics_list)
    latencies = [m["latency_ms"] for m in claim_metrics_list]
    tokens = [m["tokens"] for m in claim_metrics_list]

    def pct(num):
        return round(num / n * 100.0, 1)

    return {
        "claims_evaluated": len(claim_metrics_list),
        "outcome_pass_rate_pct": pct(outcome_pass),
        "trajectory_pass_rate_pct": pct(trajectory_pass),
        "sequence_pass_rate_pct": pct(seq_pass),
        "gap_pp": round((outcome_pass - trajectory_pass) / n * 100.0, 1),
        "tool_choice_accuracy": round(
            sum(m["tool_choice_accuracy"] for m in claim_metrics_list) / n, 4),
        "argument_validity_rate": round(
            sum(m["argument_validity"] for m in claim_metrics_list) / n, 4),
        "step_efficiency_mean": round(
            sum(m["step_efficiency"] for m in claim_metrics_list) / n, 3),
        "step_efficiency_min": round(min(m["step_efficiency"] for m in claim_metrics_list), 3),
        "step_efficiency_max": round(max(m["step_efficiency"] for m in claim_metrics_list), 3),
        "cost_per_claim_p50": round(median(costs), 6) if costs else 0.0,
        "cost_per_claim_max": round(max(costs), 6) if costs else 0.0,
        "cost_per_claim_mean": round(sum(costs) / n, 6),
        "cost_per_claim_min": round(min(costs), 6) if costs else 0.0,
        "latency_ms_p50": round(median(latencies), 1) if latencies else 0.0,
        "latency_ms_max": round(max(latencies), 1) if latencies else 0.0,
        "tokens_total": sum(tokens),
        "tokens_p50": round(median(tokens), 1) if tokens else 0.0,
        "tokens_max": max(tokens) if tokens else 0,
    }


def mode_summary(claim_metrics_list):
    """Per-mode counts for the regression table (individual modes only)."""
    counts = {m: 0 for m in MODES}
    for m in claim_metrics_list:
        for mode in m["modes"]:
            counts[mode] = counts.get(mode, 0) + 1
    return counts


def format_table(agg):
    lines = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Outcome pass rate | {agg['outcome_pass_rate_pct']}% |",
        f"| Trajectory pass rate | {agg['trajectory_pass_rate_pct']}% |",
        f"| Sequence-assertion pass rate | {agg['sequence_pass_rate_pct']}% |",
        f"| **Outcome-minus-trajectory gap** | **{agg['gap_pp']} pp** |",
        f"| Tool-choice accuracy | {agg['tool_choice_accuracy']} |",
        f"| Argument validity rate | {agg['argument_validity_rate']} |",
        f"| Step efficiency (mean / min / max) | {agg['step_efficiency_mean']} / "
        f"{agg['step_efficiency_min']} / {agg['step_efficiency_max']} |",
        f"| Cost per claim p50 / max / mean | ${agg['cost_per_claim_p50']} / "
        f"${agg['cost_per_claim_max']} / ${agg['cost_per_claim_mean']} |",
        f"| Latency ms p50 / max | {agg['latency_ms_p50']} / {agg['latency_ms_max']} |",
        f"| Tokens total / p50 / max | {agg['tokens_total']} / {agg['tokens_p50']} / "
        f"{agg['tokens_max']} |",
    ]
    return "\n".join(lines) + "\n"