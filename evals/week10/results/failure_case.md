# Injected worker failure - W6-002 (Week 10 Module 5)

## What was injected

- **Case:** `W6-002` - expected status `excluded`, clause `Exc-7.4`. This case is chosen because a missing exclusions check on it flips **excluded -> covered** (payout 0 -> 0), which is the financially material lie.
- **Failure:** the coverage/exclusions worker returns **HTTP 500** on every attempt (2 attempt(s) issued).
- **Surface:** worker boundary (in-process in this build) - the same place a real HTTP 500 from the worker endpoint would surface, before any model usage is billed. Tokens billed for the failed attempts: 0.
- **Everything else unchanged:** same prompts, same budgets, same judge (`outputs.validate_output (evals/week7/outputs.py, week-6 contract judge)`), same model `qwen/qwen3.8-27b`.

## What the orchestrator actually did (one line)

> **RETRIED then DEGRADED - 2 attempts, then a partial answer with status=null and no coverage assertion**

## Evidence - the hand-offs of the injected run

| # | Hand-off | Status | Prompt tokens | Total | Detail |
| ---: | --- | --- | ---: | ---: | --- |
| 1 | `orchestrator->planner` | 200 | 274 | 411 | manager decomposes the claim question |
| 2 | `orchestrator->coverage_worker` | HTTP500 | 0 | 0 | coverage_exclusions_worker returned HTTP 500; no model usage billed |
| 3 | `orchestrator->coverage_worker` | HTTP500 | 0 | 0 | coverage_exclusions_worker returned HTTP 500; no model usage billed |
| 4 | `orchestrator->summary_worker` | 200 | 517 | 544 | tool=get_claim status=ok |
| 5 | `orchestrator->summary_worker` | 200 | 827 | 1040 | finding emitted |

The two HTTP 500 lines carry 0 prompt tokens: the worker failed before any model usage was billed, and the orchestrator logged both attempts in the same hand-off log the multiplier is computed from.

## The answer it produced (no coverage assertion)

```json
{
  "claim_id": "W6-002",
  "claim_number": "CLM-2026-00418",
  "loss_date": "2026-03-02",
  "status": null,
  "excess": 2500,
  "exclusion_clause": null,
  "payout": null,
  "degraded": true,
  "degraded_reason": "coverage/exclusions worker returned HTTP  500 on all 2 attempt(s); no policy evidence available; no coverage decision was asserted - degraded to a partial answer instead"
}
```

- `termination_reason`: `worker_failure_degraded`
- `termination_detail`: coverage/exclusions worker returned HTTP  500 on all 2 attempt(s); no policy evidence available; no coverage decision was asserted - degraded to a partial answer instead
- Judge on the injected run: **FAIL** - failures: ["status: expected 'excluded', got None", "exclusion_clause: expected 'EXC-7.4', got None", "payout: not numeric: None"]
- Declared covered with no exclusions check? **false** (guardrail fired: false)

## Honest reading

The orchestrator **did not lie**: with no policy evidence it refused to assert a status, handed back the identity facts it had, and let the claim fail the judge instead of inventing coverage. The claim is not paid on a guess - but it is also not resolved, which is the honest cost of a dead specialist.

## If this were a real deployment

A dead coverage worker on a live claim should not silently become a failed claim: the A2A task should move to `input-required` and page an adjuster to confirm the exclusions (see `a2a_bonus.md`). The current implementation has no human-in-the-loop path, so it ends here.
