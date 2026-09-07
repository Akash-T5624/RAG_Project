from enum import Enum, auto

from claims import get_claim_record as _get_claim_record
from policy import search_policy as _search_policy

# ---------------------------------------------------------------------------
# compute_payout: claim-status enum (required by the assignment)
# ---------------------------------------------------------------------------
class ClaimStatus(Enum):
    """Final disposition of the claim used by compute_payout.

    approved: loss is covered and payable.
    excluded: a policy exclusion clause applies (no payout).
    rejected: claim denied for a reason other than a policy exclusion.
    """
    approved = "approved"
    excluded = "excluded"
    rejected = "rejected"

    @classmethod
    def coerce(cls, value):
        """Accept an Enum member, its .value, or a case-insensitive string."""
        if isinstance(value, cls):
            return value
        text = str(value).strip().lower()
        try:
            return cls(text)
        except ValueError:
            raise ValueError(
                f"claim_status must be one of "
                f"{[c.value for c in cls]}; got {value!r}"
            )


# ---------------------------------------------------------------------------
# Tool 1 (base): get_claim
# ---------------------------------------------------------------------------
def get_claim(claim_id):
    """Retrieve the structured claim record for a claim id.

    Returns the claim's number, date of loss, claim amount, excess and the
    full adjuster notes.  Retrieves the claim record only: it does not inspect
    policy exclusions and does not calculate any payout.
    """
    return _get_claim_record(claim_id)


GET_CLAIM_DESCRIPTION = (
    "Retrieve the structured claim record for the given claim id. "
    "Returns claim number, date of loss, claim amount, excess and the full "
    "adjuster notes. It only retrieves the claim; it does not search policy "
    "information and does not calculate payouts."
)


# ---------------------------------------------------------------------------
# Tool 3 (new): compute_payout  -- added for Week 7 Module 4
# ---------------------------------------------------------------------------
def compute_payout(claim_amount, excess, claim_status):
    """Calculate the final payable amount using the claim amount, policy
    excess, and claim status.

    Args:
        claim_amount (float): approved claim amount in INR.
        excess (float): policy excess/deductible in INR.
        claim_status: one of ClaimStatus (approved|excluded|rejected).

    Returns:
        {"claim_amount": float, "excess": float, "claim_status": str,
         "payout": float}  (payout = max(0, claim_amount - excess) when
         approved, else 0.0)
    """
    status = ClaimStatus.coerce(claim_status)
    if status in (ClaimStatus.approved,):
        payout = max(0.0, float(claim_amount) - float(excess))
    else:
        payout = 0.0
    return {
        "claim_amount": float(claim_amount),
        "excess": float(excess),
        "claim_status": status.value,
        "payout": round(payout, 2),
    }


COMPUTE_PAYOUT_DESCRIPTION = (
    "Calculate the final payable amount using the claim amount, the policy "
    "excess, and the claim status. It only performs the payout calculation; "
    "it does not retrieve claims and does not search policy information."
)


# ---------------------------------------------------------------------------
# Tool 2 (base): search_policy
# ---------------------------------------------------------------------------
def search_policy(query):
    """Search the policy wording for sections relevant to the query.

    Returns up to three matching passages (with page and section) covering
    coverage, exclusions and deductibles. It only searches policy information;
    it does not retrieve claims and does not calculate payouts.
    """
    return _search_policy(query)


SEARCH_POLICY_DESCRIPTION = (
    "Search the policy wording for the passages most relevant to the given "
    "query. Returns up to 3 passages covering coverage, exclusions and "
    "deductibles. It only searches policy information; it does not retrieve "
    "claim records and does not calculate payouts."
)


# ---------------------------------------------------------------------------
# Tool registry (used by the agent prompt and the workflow)
# ---------------------------------------------------------------------------
TOOLS = {
    "get_claim": {
        "function": get_claim,
        "description": GET_CLAIM_DESCRIPTION,
        "parameters": {"claim_id": {"type": "string", "required": True}},
    },
    "search_policy": {
        "function": search_policy,
        "description": SEARCH_POLICY_DESCRIPTION,
        "parameters": {"query": {"type": "string", "required": True}},
    },
    "compute_payout": {
        "function": compute_payout,
        "description": COMPUTE_PAYOUT_DESCRIPTION,
        "parameters": {
            "claim_amount": {"type": "number", "required": True},
            "excess": {"type": "number", "required": True},
            "claim_status": {
                "type": "string",
                "required": True,
                "enum": [c.value for c in ClaimStatus],
            },
        },
    },
}


def call_tool(name, args):
    """Execute a tool by registry name. Returns the tool's dict result."""
    if name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOLS[name]["function"](**args)


def tool_descriptions_block():
    """Human-readable tool catalogue injected into the agent system prompt."""
    lines = []
    for name in ("get_claim", "search_policy", "compute_payout"):
        spec = TOOLS[name]
        params = ", ".join(
            f"{p}({v['type']})" for p, v in spec["parameters"].items()
        )
        lines.append(f"- {name}({params}): {spec['description']}")
    return "\n".join(lines)