# Taxonomy — Week 5 Error Analysis (Track D · Insurance claims)

Sample: 27 eval cases (eval_set.jsonl) built from the Week 5 failure taxonomy.
Pipeline version at sampling time: `v1-hybrid-rrf60`
Judge criterion (Week 6): "Is the claim summary acceptable to route to a claims
supervisor?" — binary ACCEPT/REJECT, judged ONLY on faithfulness/completeness;
format checks (claim number, date, excess, exclusion citation) are handled by
deterministic assertions, not the judge.

## Summary-quality modes (Week 5 taxonomy used by eval_set.jsonl)

These are the Week 5 failure categories this project already used. Every eval
case is tagged with exactly ONE mode.

| # | Failure mode | Count | What it means | Example id |
|---|--------------|------:|---------------|------------|
| 1 | omitted-exclusion | 8 | Summary fails to reflect a stated denial / exclusion | W6-002 |
| 2 | hallucinated-coverage | 5 | Summary invents coverage not in the notes | W6-001 |
| 3 | release-note-summarised | 4 | Summary paraphrases instead of capturing exact release-note facts | W6-009 |
| 4 | wrong-claim-number | 4 | Claim number format/echo error | W6-003 |
| 5 | missing-loss-date | 3 | Date of loss absent / not parseable | W6-005 |
| 6 | excess-not-numeric | 3 | Excess/deductible not numeric | W6-007 |

## Retrieval modes (used by the hierarchical before/after experiment)

The Week 6 main improvement experiment (hierarchical parent-context retrieval)
also exercises retrieval-side failure modes from Week 5:

| # | Failure mode | Count | What it means | Example id |
|---|--------------|------:|---------------|------------|
| 1 | hierarchical_context | 2 | Child chunk loses parent/section context ("STEP 1", "3 steps") | rc_003 |
| 2 | spelling_error | 1 | User query contains typos ("repining a kesibilty") | rc_005 |
| 3 | grammar_variation | 1 | Query form differs from policy wording | rc_006 |
| 4 | exact_value | 2 | Exact number/id retrieval (policy number, reg no., GSTIN) | rc_001 |
| 5 | retrieval_failure | 2 | Correct chunk not retrieved (renewal info) | rc_008 |

## Week 5 → Week 6 regression mapping

```
Week 5 failure                                      Week 6 test
----------------------------------------------------------------------
Q trace 4cb9d33f — "tyres and tubes damage cover"   regression_001
  child chunk loses parent context / no coverage    category = hierarchical
Q trace a2d35a99 — "policy number?" retrieval miss  regression_002
  exact-value retrieval failure                     category = retrieval
Q07-like child-chunk-loses-parent-context           rc_003 / rc_004
Q08-like spelling mistake retrieval failure         rc_005
```

## Fix target for next week

**Mode:** hierarchical_context / parent-context retrieval

**Why this one first:** the deepest failure is loss of section context when a
retrieved child chunk stands alone (anaphora such as "these items", "STEP 1").
Recovering the parent page turns an ambiguous child into a well-grounded one.

Full falsifiable prediction: see [prediction.md](../prediction.md) and
[week6/prediction.txt](week6/prediction.txt).