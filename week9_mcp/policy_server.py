"""First-party policy-document MCP server.

This server deliberately has no model client.  It is an MCP capability
provider: the host decides when a model should call this tool.
"""

from fastmcp import FastMCP

mcp = FastMCP("Policy document search")

POLICY_PASSAGES = [
    {
        "section": "Own Damage Cover",
        "page": 12,
        "text": "Accidental loss or damage to the insured two-wheeler is covered subject to policy terms.",
    },
    {
        "section": "General Exclusions",
        "page": 18,
        "text": "Loss while the vehicle is used outside the limitations as to use is excluded.",
    },
    {
        "section": "Deductible",
        "page": 20,
        "text": "The applicable compulsory deductible is borne by the insured for each own-damage claim.",
    },
]


@mcp.tool
def search_policy_documents(query: str) -> dict:
    """Search the first-party policy corpus for wording needed to answer a coverage question.

    Use this tool when the user asks about policy coverage, exclusions, or a
    deductible. Pass a short, specific topic rather than a whole claim story.
    Treat a `recoverable: true` response as a retrieval miss: tell the user no
    policy wording was found, ask for a policy section/topic to search, and do
    not invent coverage. This tool is read-only and does not retrieve claim
    status, adjuster notes, or decide a claim.
    """
    normalized = (query or "").strip().lower()
    words = {word for word in normalized.replace("-", " ").split() if len(word) > 2}
    matches = [
        passage for passage in POLICY_PASSAGES
        if words & set((passage["section"] + " " + passage["text"]).lower().split())
    ]
    if not matches:
        return {
            "recoverable": True,
            "error": {
                "code": "POLICY_CONTEXT_NOT_FOUND",
                "message": (
                    f"No policy passage found for {query!r}. Try a coverage, exclusion, or deductible topic; "
                    "do not infer policy terms from an empty search."
                ),
            },
            "matches": [],
        }
    return {"recoverable": False, "matches": matches[:3]}


@mcp.resource("policy://exclusions-summary")
def exclusions_summary() -> str:
    """A small app-attached policy resource, not a model-invoked tool."""
    return "General exclusions: use outside limitations, illegal racing, and intentional loss are not covered."


@mcp.prompt
def coverage_review_guidance() -> str:
    """Reusable host prompt for reviewing policy wording after retrieval."""
    return (
        "Use retrieved policy text as evidence. Separate quoted policy facts from any claim facts; "
        "state when the policy corpus does not contain enough evidence."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
