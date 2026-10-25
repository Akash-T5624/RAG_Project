"""Tool registry: local tools plus whatever the MCP server advertises at runtime.

The wording-search tool is NOT named anywhere in the agent. It arrives from tools/list. A test
(tests/test_capstone.py) fails if its name appears in this package's source.
"""

from capstone.core import policies

LOCAL_TOOLS = {
    "get_claim": {
        "fn": policies.get_claim,
        "description": "Retrieve the FNOL record for a claim id: claim number, product, policy number, loss date and the FNOL text. Read-only.",
        "params": {"claim_id": "string"},
    },
    "lookup_policy": {
        "fn": policies.lookup_policy,
        "description": "Look up a policy by policy number: product, policy period, deductible, per-peril sub_limits and policy_limit. "
                       "These are three different numbers. Read-only; it does not say whether anything is covered.",
        "params": {"policy_number": "string"},
    },
    "check_coverage_in_force": {
        "fn": policies.check_coverage_in_force,
        "description": "Check whether the policy was in force on the loss date. Returns a fact (in_force true/false and the period), "
                       "not a coverage decision. Loss date must be YYYY-MM-DD from the claim record.",
        "params": {"policy_number": "string", "loss_date": "string"},
    },
    "refer_to_adjuster": {
        "fn": policies.refer_to_adjuster,
        "description": "Refer the claim to a human adjuster with a short factual reason. Every claim ends with exactly one referral; "
                       "the adjuster decides, never this system.",
        "params": {"claim_id": "string", "reason": "string"},
    },
}


def describe_mcp_tool(tool):
    props = (tool.get("inputSchema") or {}).get("properties", {})
    params = ", ".join(f"{k}({v.get('type', 'any')})" for k, v in props.items() if k != "trace_id")
    return f"{tool['name']}({params}): {' '.join((tool.get('description') or '').split())}"


def tools_block(mcp_tools):
    lines = [f"- {n}({', '.join(f'{p}({t})' for p, t in s['params'].items())}): {s['description']}"
             for n, s in LOCAL_TOOLS.items()]
    lines += [f"- {describe_mcp_tool(t)}" for t in mcp_tools]
    return "\n".join(lines)
