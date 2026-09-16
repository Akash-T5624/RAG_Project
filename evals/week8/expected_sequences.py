"""Expected tool sequences for the 10 trajectory cases.

The sequences are asserted in code as *sets of valid paths*, not one rigid
string: where a legitimate alternate path exists (a clean claim MAY open the
policy before computing the payout, or may not), both are accepted.  This is
the "assert as a set, not a single sequence" requirement from task set D.

Path grammar (per claim)
------------------------
Every valid path:
  1. get_claim             first tool call
  2. [search_policy]       for EXCLUSION-CITE claims: exactly 1, after
                           get_claim, before compute_payout
                           for CLEAN claims: 0 or 1, after get_claim, before
                           compute_payout
  3. compute_payout        exactly 1, last tool call
  4. final answer          produced only after compute_payout
"""

from trajectory_config import BRANCHING_CLAIM_IDS

TOOL_ALIASES = ("get_claim", "search_policy", "compute_payout")


def expected_spec(claim_id):
    """Return the asserted spec: required/optional tools + order constraints."""
    branching = claim_id in BRANCHING_CLAIM_IDS
    required = ["get_claim", "compute_payout"]
    optional = ["search_policy"]
    if branching:
        required = ["get_claim", "search_policy", "compute_payout"]
        optional = []
    return {
        "claim_id": claim_id,
        "branching": branching,
        "required_tools": required,
        "optional_tools": optional,
        "min_steps": len(required),
        "max_steps": len(required) + len(optional),
    }


def validate_sequence(claim_id, steps):
    """Validate one run's tool-call steps against the asserted spec.

    Returns (ok: bool, reason: str | None, accepted_path: str).
    ``steps`` is the ordered list of tool-call dicts with 'tool' and 'args'.
    """
    tools = [s.get("tool") for s in steps]
    if not tools:
        return False, "no tool calls recorded", ""

    # 1. get_claim first
    if tools[0] != "get_claim":
        return (False,
                f"first tool call was '{tools[0]}', expected 'get_claim'",
                "")

    counts = {t: tools.count(t) for t in set(tools)}
    if counts.get("get_claim", 0) < 1:
        return False, "get_claim never called", ""
    if counts.get("get_claim", 0) > 1:
        return False, f"get_claim called {counts['get_claim']} times", ""

    spec = expected_spec(claim_id)
    n_payout = counts.get("compute_payout", 0)
    if n_payout != 1:
        return False, f"compute_payout called {n_payout} times, expected exactly 1", ""
    if tools[-1] != "compute_payout":
        return (False,
                "compute_payout is not the final tool call "
                f"(last tool was '{tools[-1]}')",
                "")

    n_search = counts.get("search_policy", 0)
    if spec["branching"]:
        if n_search != 1:
            return (False,
                    f"exclusion-cite claim: search_policy called {n_search} "
                    "times, expected exactly 1",
                    "")
        if tools.index("search_policy") < tools.index("get_claim"):
            return False, "search_policy called before get_claim", ""
        if tools.index("search_policy") > tools.index("compute_payout"):
            return False, "search_policy called after compute_payout", ""
        accepted = "get_claim -> search_policy -> compute_payout"
    else:
        if n_search not in (0, 1):
            return False, f"search_policy called {n_search} times, expected 0 or 1", ""
        if n_search == 1:
            if tools.index("search_policy") < tools.index("get_claim"):
                return False, "search_policy called before get_claim", ""
            if tools.index("search_policy") > tools.index("compute_payout"):
                return False, "search_policy called after compute_payout", ""
            accepted = "get_claim -> {search_policy} -> compute_payout"
        else:
            accepted = "get_claim -> compute_payout"

    return True, None, accepted


def sequence_report():
    """Human-readable table of the 10 asserted expected sequences."""
    from trajectory_config import TRAJECTORY_CLAIM_IDS

    rows = []
    for cid in TRAJECTORY_CLAIM_IDS:
        spec = expected_spec(cid)
        if spec["branching"]:
            path = "get_claim -> search_policy -> compute_payout"
            note = "alternate paths: none (exclusion must be opened via policy)"
        else:
            path = "get_claim -> {search_policy?} -> compute_payout"
            note = ("alternate paths: 2 legitimate sets accepted — "
                    "with or without the policy lookup")
        rows.append({
            "claim_id": cid,
            "branching": spec["branching"],
            "required": spec["required_tools"],
            "optional": spec["optional_tools"],
            "asserted_path": path,
            "alternate_paths": note,
        })
    return rows