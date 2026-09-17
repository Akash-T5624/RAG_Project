"""Expected tool sequences for the 10 trajectory cases.

The sequences are asserted in code as *sets of valid paths*, not one rigid
string: where a legitimate alternate path exists, both are accepted.  This is
the "assert as a set, not a single sequence" requirement from task set D.

Week-8 extended tool registry (six tools):
  get_claim(claim_id)                     required, first tool call
  compute_payout(...)                     required, last tool call
  search_policy(query)                    policy lookup (BM25, full index)
  retrieve_policy_exclusions(topic)       policy lookup (exclusions view)
  validate_claim_number(claim_number)     optional read-only format check
  escalate_claim(claim_id, reason)        NEVER part of a valid path for this
                                          batch (no claim needs adjudicator
                                          escalation); any call is an
                                          extra-tool trajectory failure

Path grammar (per claim)
-----------------------
The "POLICY" step is a group: exactly one of {search_policy,
retrieve_policy_exclusions}.

   clean      get_claim -> [validate_claim_number?] -> [POLICY?]
              -> compute_payout
   branching  get_claim -> [validate_claim_number?] -> POLICY(1) ->
              -> compute_payout

So clean claims accept 12 legitimate sets (lookup omitted/resolved through
either policy tool, validation optional), branching claims accept 4 sets.
Everything outside those sets (escalate_claim, a second policy lookup, a
second validation, out-of-order calls) fails the sequence assertion.
"""

from trajectory_config import BRANCHING_CLAIM_IDS

TOOL_ALIASES = (
    "get_claim",
    "search_policy",
    "retrieve_policy_exclusions",
    "validate_claim_number",
    "escalate_claim",
    "compute_payout",
)

# The policy-lookup group: exactly one of these opens the policy wording.
POLICY_GROUP = ("search_policy", "retrieve_policy_exclusions")

# Tools that can never appear in a valid trajectory for this case batch.
NEVER_TOOLS = ("escalate_claim",)


def expected_spec(claim_id):
    """Return the asserted spec: required/optional tools + order constraints.

    ``required_tools`` and ``optional_tools`` are concrete tool names;
    ``policy_group`` names the lookup-group so validators can check "exactly
    one policy lookup" instead of "exactly search_policy".
    """
    branching = claim_id in BRANCHING_CLAIM_IDS
    required = ["get_claim", "compute_payout"]
    optional = ["validate_claim_number"]
    if branching:
        min_steps = len(required) + 1      # get_claim + POLICY + compute_payout
        max_steps = len(required) + 2      # + validate_claim_number
    else:
        min_steps = len(required)
        max_steps = len(required) + 2      # + validate + one policy lookup
    return {
        "claim_id": claim_id,
        "branching": branching,
        "required_tools": required,
        "optional_tools": optional,
        "policy_group": POLICY_GROUP,
        "policy_required": branching,
        "min_steps": min_steps,
        "max_steps": max_steps,
    }


def _fail(reason):
    return False, reason, ""


def validate_sequence(claim_id, steps):
    """Validate one run's tool-call steps against the asserted spec.

    Returns (ok: bool, reason: str | None, accepted_path: str).
    ``steps`` is the ordered list of tool-call dicts with 'tool' and 'args'.
    """
    tools = [s.get("tool") for s in steps]
    if not tools:
        return _fail("no tool calls recorded")

    # 1. get_claim opens the run and is called exactly once.
    if tools[0] != "get_claim":
        return _fail(f"first tool call was '{tools[0]}', expected 'get_claim'")
    n_get = tools.count("get_claim")
    if n_get != 1:
        return _fail(f"get_claim called {n_get} times, expected exactly 1")

    # 2. compute_payout is called exactly once and is the last tool call.
    n_payout = tools.count("compute_payout")
    if n_payout != 1:
        return _fail(f"compute_payout called {n_payout} times, expected exactly 1")
    if tools[-1] != "compute_payout":
        return _fail(
            f"compute_payout is not the final tool call "
            f"(last tool was '{tools[-1]}')")

    # 3. validate_claim_number: at most once, between claim and payout.
    n_validate = tools.count("validate_claim_number")
    if n_validate > 1:
        return _fail(f"validate_claim_number called {n_validate} times, expected 0 or 1")
    if n_validate == 1 and tools.index("validate_claim_number") in (0, len(tools) - 1):
        return _fail("validate_claim_number must come after get_claim and "
                     "before compute_payout")

    # 4. the POLICY group: exactly one lookup on branching claims, 0 or 1 on
    #    clean claims, always between claim and payout.
    policy_tools = [t for t in tools if t in POLICY_GROUP]
    n_policy = len(policy_tools)
    spec = expected_spec(claim_id)
    if spec["policy_required"]:
        if n_policy != 1:
            return _fail(
                f"exclusion-cite claim: policy lookup called {n_policy} times "
                "(exactly one of {search_policy, retrieve_policy_exclusions} "
                "is required)")
    else:
        if n_policy > 1:
            return _fail(
                f"clean claim: policy lookup called {n_policy} times, expected 0 or 1")
    for t in policy_tools:
        idx = tools.index(t)
        if idx in (0, len(tools) - 1) or idx < tools.index("get_claim") \
                or idx > tools.index("compute_payout"):
            return _fail(
                f"{t} must come after get_claim and before compute_payout")

    # 5. no tool outside the accepted set (escalate_claim is never valid here).
    accepted = set(spec["required_tools"]) | set(spec["optional_tools"]) | set(
        spec["policy_group"])
    extra = [t for t in tools if t not in accepted]
    if extra:
        return _fail(
            f"tool call outside the accepted trajectory set: {extra} "
            "(escalate_claim is not part of any valid path for this batch)")

    path = _describe_accepted(tools, spec)
    return True, None, path


def _describe_accepted(tools, spec):
    """Human-readable rendering of the actual path taken (for the report)."""
    parts = []
    for t in tools:
        parts.append(t if t != "get_claim" else "get_claim")
    return " -> ".join(parts)


def sequence_report():
    """Human-readable table of the 10 asserted expected sequences."""
    from trajectory_config import TRAJECTORY_CLAIM_IDS

    rows = []
    for cid in TRAJECTORY_CLAIM_IDS:
        spec = expected_spec(cid)
        if spec["branching"]:
            path = ("get_claim -> [validate_claim_number?] -> "
                    "(search_policy | retrieve_policy_exclusions) "
                    "-> compute_payout")
            note = ("alternate paths: 4 legitimate sets accepted — exactly one "
                    "policy lookup via either policy tool, validation optional")
        else:
            path = ("get_claim -> [validate_claim_number?] -> "
                    "[search_policy | retrieve_policy_exclusions?] "
                    "-> compute_payout")
            note = ("alternate paths: 12 legitimate sets accepted — policy "
                    "lookup may be omitted or resolved through either policy "
                    "tool; validation optional")
        rows.append({
            "claim_id": cid,
            "branching": spec["branching"],
            "required": spec["required_tools"],
            "optional": spec["optional_tools"],
            "asserted_path": path,
            "alternate_paths": note,
        })
    return rows