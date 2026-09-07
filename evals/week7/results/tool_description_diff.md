# Tool descriptions: Agent vs Fixed Workflow (Week 7 Module 4)

Same three tools, same implementations (`evals/week7/tools.py`), for BOTH systems.
The difference is *who reads the descriptions*:

- **Agent**: the LLM decides each next step, so the model is given the full tool
  descriptions in its system prompt (`tool_descriptions_block()`). Reads the block at
  every decision call.
- **Fixed workflow**: the path is deterministic code. No LLM exists in the pipeline,
  so **no tool descriptions are consumed**; code calls the same tool functions directly.

## What the agent's model receives

```
- get_claim(claim_id(string)): Retrieve the structured claim record for the given claim id. Returns claim number, date of loss, claim amount, excess and the full adjuster notes. It only retrieves the claim; it does not search policy information and does not calculate payouts.
- search_policy(query(string)): Search the policy wording for the passages most relevant to the given query. Returns up to 3 passages covering coverage, exclusions and deductibles. It only searches policy information; it does not retrieve claim records and does not calculate payouts.
- compute_payout(claim_amount(number), excess(number), claim_status(string)): Calculate the final payable amount using the claim amount, the policy excess, and the claim status. It only performs the payout calculation; it does not retrieve claims and does not search policy information.
```

## What the workflow reads

```
nothing - the workflow is hard-coded (get_claim -> search_policy -> compute_payout)
```

## Diff

```diff
- Agent: tool descriptions block of N lines, replayed on every LLM call.
+ Workflow: no LLM, no tool descriptions; identical tool functions invoked by code.
```

The identical tool implementations keep the comparison fair: the only difference is
whether a model (which needs descriptions) or code (which does not) drives the tools.
