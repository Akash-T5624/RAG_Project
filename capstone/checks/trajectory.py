"""Trajectory eval on the integrated agent (Week 8 method, new tool set).

Computed from the SAME runs as the suite (no extra model calls). Expected path per case:
  get_claim first -> [check_coverage_in_force] -> wording search (only after the in-force check)
  -> lookup_policy -> refer_to_adjuster last. Differences from Week 8: referral is now REQUIRED, and a
  wording search with a made-up date on a case that has none is a violation.
Reports tool-choice accuracy, argument validity, trajectory pass, outcome pass and the gap in pp.
"""

from capstone.agent.tools import LOCAL_TOOLS


def expected_spec(case):
    dated = bool(case.get("loss_date"))
    in_force = case["expect"].get("in_force")
    return {
        "required": ["get_claim", "refer_to_adjuster", "lookup_policy"] + (["check_coverage_in_force"] if dated else []),
        "search_required": dated and in_force is not False,
        "search_forbidden": not dated,
        "allowed": set(LOCAL_TOOLS),  # plus the discovered wording tool, added per run below
    }


def judge_trajectory(case, run):
    spec = expected_spec(case)
    tools = run.get("tools_called", [])
    search_tools = [t for t in tools if t not in LOCAL_TOOLS]
    fails = []
    if not tools or tools[0] != "get_claim":
        fails.append("get_claim not first")
    for t in spec["required"]:
        if t not in tools:
            fails.append(f"missing {t}")
    if tools and tools[-1] != "refer_to_adjuster":
        fails.append("referral not last")
    if spec["search_required"] and not search_tools:
        fails.append("no wording search on an in-force dated claim")
    if spec["search_forbidden"] and search_tools:
        fails.append("wording search with no loss date")
    if search_tools and "check_coverage_in_force" in tools:
        if tools.index("check_coverage_in_force") > tools.index(search_tools[0]):
            fails.append("searched before in-force check")
    on_path = [t for t in tools if t in spec["allowed"] or t in search_tools]
    tool_choice_acc = (len(on_path) / len(tools)) if tools else 0.0
    steps = run.get("steps", [])
    bad_args = sum(1 for s in steps if s["status"] == "error") + len(run.get("args_overridden", []))
    min_steps = len(spec["required"]) + (1 if spec["search_required"] else 0)
    return {"trajectory_pass": not fails, "fails": fails, "tool_choice_accuracy": tool_choice_acc,
            "arg_valid_calls": len(steps) - bad_args, "calls": len(steps), "steps": len(steps), "min_steps": min_steps}


def aggregate(rows):
    n = len(rows) or 1
    outcome = sum(r["outcome_pass"] for r in rows) / n * 100
    traj = sum(r["trajectory_pass"] for r in rows) / n * 100
    calls = sum(r["calls"] for r in rows) or 1
    return {
        "n": len(rows),
        "outcome_pass_pct": round(outcome, 1),
        "trajectory_pass_pct": round(traj, 1),
        "outcome_minus_trajectory_gap_pp": round(outcome - traj, 1),
        "tool_choice_accuracy": round(sum(r["tool_choice_accuracy"] for r in rows) / n, 3),
        "argument_validity_pct": round(sum(r["arg_valid_calls"] for r in rows) / calls * 100, 1),
        "mean_steps_over_min": round(sum(r["steps"] / max(1, r["min_steps"]) for r in rows) / n, 2),
    }
