"""Trajectory metrics for the generic RAG agent: the four numbers + the gap.

Two separate pass/fail signals, deliberately kept apart:
  outcome_pass     does the final answer carry every expected keyword?
  trajectory_pass  did the tool-call sequence match one of the accepted paths?

gap = outcome_pass_rate - trajectory_pass_rate.  A case that passes the
outcome eval but fails the trajectory eval is a false positive trace — it got
the right answer for the wrong process and must be surfaced, not averaged
away.  Cost and latency are aggregated as p50 and max, never a bare mean, so
a single runaway case cannot disappear into the average.
"""

import re
from statistics import median

from injection_defense import sanitize_tool_output  # noqa: F401 (re-exported hooks)

# Per-tool input well-formedness rules shared by compute_argument_validity.
# Each rule receives the kwargs dict and returns None (valid) or a reason.
def _arg_validity_rules():
    return {
        "retrieve": [
            ("query present", lambda a: bool((a.get("query") or "").strip())),
        ],
        "check_deprecation": [
            ("content present", lambda a: bool((a.get("content") or "").strip())),
        ],
        "answer": [
            ("context present", lambda a: bool((a.get("context") or "").strip())),
            ("question present", lambda a: bool((a.get("question") or "").strip())),
        ],
    }


def trajectory_matches(actual_steps, accepted_paths):
    """True when the exact ordered tool sequence equals an accepted path."""
    actual_tools = [s["action"]["tool"] for s in actual_steps]
    return any(actual_tools == path for path in accepted_paths)


def outcome_passes(case, answer):
    """True when the answer carries EVERY expected keyword (case-insensitive)."""
    lowered = (answer or "").lower()
    return all(kw.lower() in lowered for kw in case.get("expected_keywords", []))


def compute_tool_choice_accuracy(steps, case):
    """% of steps where the chosen tool matches ANY accepted path at its
    position.  A step at index i is correct if some accepted path has that
    same tool at position i; steps beyond the longest path are always wrong.
    """
    if not steps:
        return 0.0
    accepted_paths = case.get("accepted_tool_paths", [])
    correct = sum(
        1 for i, step in enumerate(steps)
        if any(i < len(p) and p[i] == step["action"]["tool"]
               for p in accepted_paths)
    )
    return correct / len(steps)


def compute_argument_validity(steps):
    """% of tool calls whose inputs pass the per-tool well-formedness rules."""
    if not steps:
        return 0.0
    rules = _arg_validity_rules()
    valid = 0
    for step in steps:
        tool = step["action"]["tool"]
        args = step["action"].get("args") or {}
        checks = rules.get(tool, [])
        if all(pred(args) for _label, pred in checks):
            valid += 1
    return valid / len(steps)


def compute_step_efficiency(case, steps):
    """optimal / actual, capped at 1.0; no steps yields 0.0."""
    if not steps:
        return 0.0
    return min(1.0, case.get("optimal_steps", 1) / len(steps))


def _tool_costs(tokens, price_per_1m):
    """Split a token count into input/output halves at a flat per-1M rate."""
    return tokens * (price_per_1m / 1_000_000)


def process_case_metrics(case, steps, answer, *, tokens, latency_ms, cost,
                         error=None, price_per_1m=None):
    """Build the per-case metric dict (the raw material for aggregation)."""
    if price_per_1m is not None:
        cost = _tool_costs(tokens, price_per_1m) if cost is None else cost
    actual_tools = [s["action"]["tool"] for s in steps]
    op = outcome_passes(case, answer)
    tp = trajectory_matches(steps, case.get("accepted_tool_paths", []))
    return {
        "case_id": case["id"],
        "question": case.get("question", ""),
        "tool_names": actual_tools,
        "step_count": len(actual_tools),
        "optimal_steps": case.get("optimal_steps", 0),
        "failure_mode": case.get("failure_mode"),
        "injection_attack": case.get("injection_attack"),
        "outcome_pass": bool(op),
        "trajectory_pass": bool(tp),
        "tool_choice_accuracy": round(compute_tool_choice_accuracy(steps, case), 4),
        "argument_validity": round(compute_argument_validity(steps), 4),
        "step_efficiency": round(compute_step_efficiency(case, steps), 4),
        "cost": round(cost or 0.0, 8),
        "latency_ms": round(latency_ms or 0.0, 1),
        "tokens": int(tokens or 0),
        "answer": (answer or ""),
        "error": error,
    }


def aggregate_results(metrics_list):
    """Roll per-case metrics into the aggregated report numbers."""
    n = len(metrics_list) or 1
    outcome_pass = sum(1 for m in metrics_list if m["outcome_pass"])
    trajectory_pass = sum(1 for m in metrics_list if m["trajectory_pass"])

    costs = sorted(m["cost"] for m in metrics_list)
    latencies = [m["latency_ms"] for m in metrics_list]
    token_list = [m["tokens"] for m in metrics_list]

    p50 = lambda values: median(values) if values else 0.0
    pct = lambda num: round(num / n * 100.0, 1)

    by_mode = {}
    for m in metrics_list:
        key = m.get("failure_mode") or "no_failure"
        entry = by_mode.setdefault(key, {"count": 0, "outcome_pass_count": 0})
        entry["count"] += 1
        if m["outcome_pass"]:
            entry["outcome_pass_count"] += 1

    false_positives = [
        m for m in metrics_list if m["outcome_pass"] and not m["trajectory_pass"]
    ]

    return {
        "cases_run": len(metrics_list),
        "outcome_pass_rate_pct": pct(outcome_pass),
        "trajectory_pass_rate_pct": pct(trajectory_pass),
        "gap_pp": round((outcome_pass - trajectory_pass) / n * 100.0, 1),
        "false_positive_count": len(false_positives),
        "false_positive_cases": [m["case_id"] for m in false_positives],
        "tool_choice_accuracy_mean": round(
            sum(m["tool_choice_accuracy"] for m in metrics_list) / n, 4),
        "argument_validity_mean": round(
            sum(m["argument_validity"] for m in metrics_list) / n, 4),
        "step_efficiency_mean": round(
            sum(m["step_efficiency"] for m in metrics_list) / n, 4),
        "cost_per_case_p50": round(p50(costs), 8),
        "cost_per_case_max": round(max(costs), 8) if costs else 0.0,
        "cost_per_case_mean": round(sum(costs) / n, 8),
        "latency_ms_p50": round(p50(latencies), 1),
        "latency_ms_max": round(max(latencies), 1) if latencies else 0.0,
        "tokens_total": sum(token_list),
        "tokens_p50": round(p50(token_list), 1),
        "tokens_max": max(token_list) if token_list else 0,
        "by_mode": by_mode,
    }


def format_table(agg, title="Trajectory eval"):
    lines = [
        f"## {title}",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Outcome pass rate | {agg['outcome_pass_rate_pct']}% |",
        f"| Trajectory pass rate | {agg['trajectory_pass_rate_pct']}% |",
        f"| **Outcome-minus-trajectory gap** | **{agg['gap_pp']} pp** |",
        f"| False-positive traces (outcome pass, trajectory fail) | "
        f"{agg['false_positive_count']} — {agg['false_positive_cases']} |",
        f"| Tool-choice accuracy (mean) | {agg['tool_choice_accuracy_mean']} |",
        f"| Argument validity (mean) | {agg['argument_validity_mean']} |",
        f"| Step efficiency (mean) | {agg['step_efficiency_mean']} |",
        f"| Cost per case p50 / max | ${agg['cost_per_case_p50']} / "
        f"${agg['cost_per_case_max']} |",
        f"| Latency ms p50 / max | {agg['latency_ms_p50']} / "
        f"{agg['latency_ms_max']} |",
        f"| Tokens total / p50 / max | {agg['tokens_total']} / "
        f"{agg['tokens_p50']} / {agg['tokens_max']} |",
        "",
    ]
    return "\n".join(lines)