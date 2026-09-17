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
# Week-8 extension — Tool 4 (new): retrieve_policy_exclusions
# ---------------------------------------------------------------------------
def retrieve_policy_exclusions(topic):
    """Search the policy's exclusion wording for passages relevant to topic.

    A deterministic, exclusions-tuned view of the same policy index: passages
    that mention exclusions rank ahead of general coverage passages.  Read-only:
    it does not retrieve claims and does not calculate payouts.
    """
    hits = search_policy(topic) or []
    timed = []
    for h in hits:
        text = " ".join([h.get("section", ""), h.get("snippet", "")]).lower()
        timed.append((0 if "exclu" in text else 1, h))
    timed.sort(key=lambda x: x[0])
    return [h for _, h in timed][:3]


RETRIEVE_EXCLUSIONS_DESCRIPTION = (
    "Search the policy's exclusion wording for the passages most relevant to "
    "the given topic. Returns up to 3 exclusion-related passages (page and "
    "section). It is a read-only view of the policy; it does not retrieve "
    "claim records and does not calculate payouts."
)


# ---------------------------------------------------------------------------
# Week-8 extension — Tool 5 (new): validate_claim_number
# ---------------------------------------------------------------------------
import re as _re

CLAIM_NUMBER_RE = _re.compile(r"^CLM-\d{4}-\d{5}$", _re.IGNORECASE)


def validate_claim_number(claim_number):
    """Read-only format check of a claim number against the claim registry
    pattern CLM-YYYY-NNNNN."""
    value = str(claim_number or "").strip()
    ok = bool(CLAIM_NUMBER_RE.match(value))
    return {
        "claim_number": value,
        "expected_format": "CLM-YYYY-NNNNN",
        "format_valid": ok,
        "detail": (
            "format matches the claim registry pattern"
            if ok else "format does not match the claim registry pattern "
                       "(CLM-YYYY-NNNNN)"
        ),
    }


VALIDATE_CLAIM_NUMBER_DESCRIPTION = (
    "Read-only format check of a claim number against the claim registry "
    "pattern CLM-YYYY-NNNNN. Returns whether the format is valid. It never "
    "changes any data and it does not retrieve or calculate anything."
)


# ---------------------------------------------------------------------------
# Week-8 extension — Tool 6 (new): escalate_claim (the only write-like tool)
# ---------------------------------------------------------------------------
def escalate_claim(claim_id, reason):
    """Route the claim to a human adjudicator for manual review.

    The only WRITE/action tool in the registry.  It does NOT disburse any
    payment: settlement always flows through compute_payout's pegged numbers.
    Should be called only when a claim genuinely needs manual review (e.g.
    suspected fraud, missing police report).
    """
    return {
        "claim_id": str(claim_id),
        "reason": str(reason or ""),
        "escalated": True,
        "note": "Claim routed for human adjudicator review. No payment is "
                "disbursed by this tool.",
    }


ESCALATE_CLAIM_DESCRIPTION = (
    "Route the claim to a human adjudicator for manual review. This is a write "
    "action with human consequences and it does NOT disburse any payment. Call "
    "it ONLY when a claim genuinely needs manual review (suspected fraud, "
    "missing police report); routine approvals and standard exclusions must "
    "never be escalated."
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
    "retrieve_policy_exclusions": {
        "function": retrieve_policy_exclusions,
        "description": RETRIEVE_EXCLUSIONS_DESCRIPTION,
        "parameters": {"topic": {"type": "string", "required": True}},
    },
    "validate_claim_number": {
        "function": validate_claim_number,
        "description": VALIDATE_CLAIM_NUMBER_DESCRIPTION,
        "parameters": {"claim_number": {"type": "string", "required": True}},
    },
    "escalate_claim": {
        "function": escalate_claim,
        "description": ESCALATE_CLAIM_DESCRIPTION,
        "parameters": {
            "claim_id": {"type": "string", "required": True},
            "reason": {"type": "string", "required": True},
        },
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

# Order in which tools are advertised to the agent: base read tools first,
# the write-like action tool clearly separated, compute_payout last.
TOOL_ORDER = [
    "get_claim",
    "search_policy",
    "retrieve_policy_exclusions",
    "validate_claim_number",
    "escalate_claim",
    "compute_payout",
]


def call_tool(name, args):
    """Execute a tool by registry name. Returns the tool's dict result."""
    if name not in TOOLS:
        raise KeyError(f"Unknown tool: {name}")
    return TOOLS[name]["function"](**args)


def tool_descriptions_block():
    """Human-readable tool catalogue injected into the agent system prompt."""
    lines = []
    for name in TOOL_ORDER:
        spec = TOOLS[name]
        params = ", ".join(
            f"{p}({v['type']})" for p, v in spec["parameters"].items()
        )
        lines.append(f"- {name}({params}): {spec['description']}")
    return "\n".join(lines)