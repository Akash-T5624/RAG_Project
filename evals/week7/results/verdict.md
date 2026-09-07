# Week 7 Module 4 - Race verdict

## Final comparison table (measured 2026-09-06)

Model: `qwen/qwen3.8-27b` (Groq) - same model configuration for BOTH systems.
Evaluation data: `evals/week6/eval_set.jsonl` (27 claims); race set of 10 by the
documented deterministic rule (`W6-001..W6-006, W6-008..W6-011`).

| Metric | Agent | Fixed Workflow |
| --- | ---: | ---: |
| Pass rate | 100.0% (10/10) | 100.0% (10/10) |
| p50 latency | 31.7 s | 2.7 ms |
| Total tokens | 46,466 | 0 |
| Cost per claim | $0.004273 | $0.000000 |

Full per-claim data: `race.csv` (repo root), `results/per_claim_results.json`.

## Documented assumptions (not hidden in the numbers)

1. **Claim amount.** `evals/week6/eval_set.jsonl` carries adjuster notes but no
   claim amount. Every payout uses the documented constant `DEFAULT_CLAIM_AMOUNT
   = 30,000` (configurable via `WEEK7_CLAIM_AMOUNT`), so expected payout =
   `max(0, 30000 - excess)` for approved claims and `0` otherwise.
2. **Model.** `backend/.env` configures `openai/gpt-oss-120b`, but on this
   shared free-tier key that model returns a 429 with a multi-minute cooling
   window on any multi-call agent loop (measured), so the race uses a stable
   model on the SAME key - `qwen/qwen3.8-27b` - for both systems. Both share one
   model id, satisfying "same LLM configuration for both"; pricing is the Groq
   list price of $0.80/$4.00 per 1M input/output tokens.
3. **Workflow zero tokens.** The workflow is deterministic code (no LLM in its
   path), so 0 tokens / $0 is a property of the implementation, not a missing
   measurement.
4. **Budgets.** All four budgets are enforced inside the agent loop and logged.
   `budget_termination.log` (and `results/budget_termination_max_tokens.log`)
   show clean `max_iterations` and `max_tokens` terminations.

## Verdict (<150 words)

Both systems passed all ten claims, so the decision rests on speed and cost:
the fixed workflow is deterministic, free, and 11,000x faster (2.7 ms vs 31.7 s
p50) because it contains zero model calls; the agent reads the same tools but
charges ~$0.004 per claim. Does the path vary by input? Yes for the agent, no
for the workflow. On this dataset every coverage decision reduces to the clause
id plus denial language already present in the adjuster notes, so the workflow's
coded rule reproduces all ten labels; branching bought nothing. Verdict: keep
the fixed workflow for this dataset. Reserve the agent for claims whose deciding
fact cannot be reduced to a fixed rule; its flexible path is then the correct
mechanism, just not at a cost this dataset can justify.

(135 words)