# Top 3 known weaknesses

DRAFT written 2026-10-25 from the build and from the offline retrieval run, not from live traces.
Before Wednesday: read live traces from `run.py eval`, confirm or replace these, and date the final version. Do not fix these before they are dated.

1. **Retrieval misses paraphrased FNOLs.** With the BM25 fallback, case C-S7 (a delivery-app collision, loss 2024-10-05) does not return Exc-3.3 in the top 3,
   so the straddle hit@3 is 83.3% (5/6). The date filter is correct; ranking is the weak part. Re-measure with the hybrid ranker after installing `requirements.txt`.
2. **PII redaction is pattern-based.** A claimant name with no title or label ("Rahul Sharma" alone) is not caught, and injury redaction removes
   known terms, not anatomy ("ribs" survived in a test). The deny-list test passes only for the names, addresses and injuries in the fixtures.
3. **The compliance guard is a phrase list.** It removes "covered", "approved", "declined", "payable" and similar, so a paraphrase ("we will honour this") passes.
   The judge criterion checks for implied decisions, but its agreement is unmeasured until hand labels exist.
