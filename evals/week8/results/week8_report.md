# Week 8 · Task set D — Claims agent trajectory eval

Subject: the week-7 claims **agent** (free-form ReAct) scored on its **path**, not just its payout.

Model: `qwen/qwen3.8-27b` · 10 claims · pricing input $0.80/M output $4.00/M (Groq list).

## 1. Expected tool sequences (asserted in code, as sets)

| Claim | Branching | Asserted path set | Alternate paths accepted |
| --- | --- | --- | --- |
| W6-001 | False | `get_claim -> {search_policy?} -> compute_payout` | alternate paths: 2 legitimate sets accepted — with or without the policy lookup |
| W6-002 | True | `get_claim -> search_policy -> compute_payout` | alternate paths: none (exclusion must be opened via policy) |
| W6-003 | False | `get_claim -> {search_policy?} -> compute_payout` | alternate paths: 2 legitimate sets accepted — with or without the policy lookup |
| W6-004 | True | `get_claim -> search_policy -> compute_payout` | alternate paths: none (exclusion must be opened via policy) |
| W6-005 | False | `get_claim -> {search_policy?} -> compute_payout` | alternate paths: 2 legitimate sets accepted — with or without the policy lookup |
| W6-006 | False | `get_claim -> {search_policy?} -> compute_payout` | alternate paths: 2 legitimate sets accepted — with or without the policy lookup |
| W6-008 | True | `get_claim -> search_policy -> compute_payout` | alternate paths: none (exclusion must be opened via policy) |
| W6-009 | False | `get_claim -> {search_policy?} -> compute_payout` | alternate paths: 2 legitimate sets accepted — with or without the policy lookup |
| W6-010 | False | `get_claim -> {search_policy?} -> compute_payout` | alternate paths: 2 legitimate sets accepted — with or without the policy lookup |
| W6-011 | True | `get_claim -> search_policy -> compute_payout` | alternate paths: none (exclusion must be opened via policy) |

## 2. Baseline (agent) — the four trajectory numbers

| Metric | Value |
| --- | ---: |
| Outcome pass rate | 100.0% |
| Trajectory pass rate | 70.0% |
| Sequence-assertion pass rate | 100.0% |
| **Outcome-minus-trajectory gap** | **30.0 pp** |
| Tool-choice accuracy | 1.0 |
| Argument validity rate | 1.0 |
| Step efficiency (mean / min / max) | 1.05 / 1.0 / 1.5 |
| Cost per claim p50 / max / mean | $0.004274 / $0.005217 / $0.004273 |
| Latency ms p50 / max | 30946.3 / 61239.4 |
| Tokens total / p50 / max | 46466 / 4647.0 / 5757 |


### 3. Outcome-vs-trajectory gap

**Gap = 100.0% (outcome) − 70.0% (trajectory) = 30.0 pp** of answers that were right by luck, not by path.

Claims passing the outcome eval while failing the trajectory eval: **W6-003, W6-005, W6-009**.

**Named right-answer-wrong-path case:** `W6-003` — outcome PASS, trajectory FAIL.

```
claim:        W6-003  (clean)
title:        Rear-end collision
steps taken:  ['get_claim', 'compute_payout']
accepted set: get_claim -> compute_payout
failure modes:['unverified_clean']
seq reason:   None
outcome:      PASS (payout computed correctly)
```
The claim passed the **outcome** eval (the payout is right) yet failed the **trajectory** eval: the wrong path it took was `unverified_clean` — it finalised `status=approved` and paid the claim while its tool trace shows no policy lookup (`['get_claim', 'compute_payout']`) and the notes did not attest that exclusions were reviewed. That is exactly the time bomb the claims director saw: the path that produced it will not generalise to a claim that looks clean but is excluded.

## 4. One mitigation: replace the free-form agent with the fixed LLM workflow

Exactly **one** mitigation was applied to the single top failure mode. The top mode on the baseline was **`unverified_clean`** (3 claims).

The mitigation chosen is *replace-the-agent-with-the-workflow* (week-7 concept, extended): the free-form ReAct loop is replaced by a fixed five-phase pipeline that **still uses the LLM to answer** — an LLM reasons over the notes + policy passages to decide the status, and code then asserts the *tool-returned* values (claim number, excess, amount, clause id) into the final answer.

| Phase | Step | LLM? |
| --- | --- | --- |
| 1 | `get_claim` (tool) | no |
| 2 | `search_policy` (tool) — opened **on every claim**, before the decision, query built by code from the claim's own title+notes | no |
| 3 | LLM decides status from notes + policy passages | **yes** |
| 4 | `compute_payout` (tool) | no |
| 5 | code assembles the final answer from tool-returned values | no |

| Run | `unverified_clean` count | Token price | Latency price | Cost/claim price |
| --- | ---: | ---: | ---: | ---: |
| Baseline agent | **3 / 10** | 46466 total | 30946.3 ms p50 | $0.004274 p50 |
| LLM workflow | **0 / 10** | 6849 total (p50 675.5) | 236.3 ms p50 | $0.000601 p50 |

Drop for `unverified_clean`: 3 → 0 (-3).

### The price of the fix (measured, not asserted free)

The workflow is **not** a zero-token cheat — it pays a measured cost per claim: **6849 total (p50 675.5)**, **236.3 ms p50**, **$0.000601 p50**, and it makes exactly one LLM decision call plus a policy lookup on **every** claim (step efficiency 1.3 vs baseline 1.05 — the workflow refuses to skip the exclusion check). It happens to be cheaper per claim than the agent loop (0.004274 → 0.000601) because it collapses the loop to one decision call — but the price is the measured latency, tokens and steps above, none of which was asserted to be zero.

## 5. Regression check — every mode before and after

| Mode | Agent (before) | Workflow (after) | Change |
| --- | ---: | ---: | --- |
| **omission** | | | |
| · skipped_search | 0 | 0 | same (+0) |
| · skipped_search_notes | 0 | 0 | same (+0) |
| · unverified_clean | 3 | 0 | improved (-3) |
| **order** | | | |
| · payout_before_claim | 0 | 0 | same (+0) |
| · search_before_claim | 0 | 0 | same (+0) |
| · final_without_claim | 0 | 0 | same (+0) |
| **fabrication** | | | |
| · fabricated_claim_number | 0 | 0 | same (+0) |
| · fabricated_payout_args | 0 | 0 | same (+0) |
| · fabricated_clause | 0 | 0 | same (+0) |
| · wrong_clause | 0 | 0 | same (+0) |
| **loop** | | | |
| · repeated_call | 0 | 0 | same (+0) |
| **wrong_tool** | | | |
| · junk_query | 0 | 0 | same (+0) |
| **quiet_give_up** | | | |
| · no_final | 0 | 0 | same (+0) |

**Honest regression read:** no mode got worse and no new mode appeared after the mitigation (the only moved number is **step efficiency** 1.05 → 1.3, the measured price of opening the exclusions on every claim). Modes checked: fabricated_claim_number, fabricated_clause, fabricated_payout_args, final_without_claim, junk_query, no_final, payout_before_claim, repeated_call, search_before_claim, skipped_search, skipped_search_notes, unverified_clean, wrong_clause.

Top mode after mitigation: **`None`** (0 claims).

## 6. What could still get through (limits)

- The indexed policy corpus contains **no `Exc-N.M` clause ids at all** (it is the fictional two-wheeler policy). Every exclusion citation in the eval data lives in the adjuster notes. So clause citation is grounded against the notes (operational record) + the policy wording; a *wrong* real-looking id that matches nothing anywhere still slips `wrong_clause`/`fabricated_clause` — and a `wrong_clause` can still pass the outcome eval if the labels disagree about the id.
- The single LLM decision inside the workflow can still be *nudged* by a prompt-injection hidden in a free-text adjuster note — that is defended separately in `injection.py`.
- `unverified_clean` only counts claims whose notes do not attest exclusions were reviewed; a claim whose notes *claim* they were reviewed passes even though the policy was never opened — an attestation is not an audit.

