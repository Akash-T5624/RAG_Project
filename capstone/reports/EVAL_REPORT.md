# Integrated eval report

All numbers below were produced by `python capstone/run.py eval` and `checks/retrieval_eval.py`; nothing is hand-typed. Data is SYNTHETIC (see non-coverage).

## 1. Loss-date-aware retrieval, no model involved (ranker: `bm25-fallback`)

Query = FNOL text (PII redacted). Same code path, filter off vs on.

| slice | n | mode | hit@3 % | wrong version in top-3 % | wrong version top-1 % |
|---|---|---|---|---|---|
| straddle | 6 | before_no_filter | 83.3 | 100.0 | 33.3 |
| straddle | 6 | after_date_filter | 83.3 | 0.0 | 0.0 |
| non_straddle | 16 | before_no_filter | 93.8 | 100.0 | 62.5 |
| non_straddle | 16 | after_date_filter | 100.0 | 0.0 | 0.0 |
| all | 22 | before_no_filter | 90.9 | 100.0 | 54.5 |
| all | 22 | after_date_filter | 95.5 | 0.0 | 0.0 |

Per-case misses after the filter: C-S7 (returned [['Exc-2.4', '2023'], ['Exc-8.1', '2023'], ['Exc-5.1', '2023']], expected ['Exc-3.3', '2023'])

## 2. Wrong-endorsement-version citation rate (the number that moved)

NOT RUN: needs both `run.py eval --before` and `run.py eval` (live model + GROQ_API_KEY).

## 3. Integrated suite, after (date filter ON)

NOT RUN.

## 3. Integrated suite, before (date filter OFF)

NOT RUN.

## 4. Judge agreement on integrated outputs

NOT RUN: `run.py judge` (needs the suite run and your hand labels).

## 5. What this suite does NOT cover

- Real policy wording: the endorsement sets, policies and limits are SYNTHETIC fixtures authored for this capstone.
- Scale: n is the case count shown, not the 500 claim files of the brief; no statistical confidence is claimed.
- Parse quality: no PDF exclusions schedule is parsed or measured; the wording store is authored JSON.
- Bare names: redaction is pattern-based; a claimant name with no title or label is not caught.
- Multi-claim and multi-policy claims, endorsements issued mid-term, and policy period vs wording-version mismatch.
- Reproducing a model answer 18 months later: retrieval is replayable from the trace, the model's wording is not guaranteed.
- Live load: throughput is extrapolated from sequential runs, not load-tested.
