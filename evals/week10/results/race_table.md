## Week 10 Module 5 - Claims squad (1 manager + 2 specialists) vs single agent

- Model: `qwen/qwen3.8-27b` - one model id, one key, both arms
- Pricing: input $0.8/1M, output $4.0/1M (Groq list price for qwen/qwen3.8-27b (console.groq.com/docs/model/qwen/qwen3.8-27b): input $0.80 / output $4.00 per 1M tokens)
- Eval set: `evals/week6/eval_set.jsonl` - the **same 10 Week-6 cases** for both arms, no new cases written: `W6-001`, `W6-002`, `W6-003`, `W6-004`, `W6-005`, `W6-006`, `W6-008`, `W6-009`, `W6-010`, `W6-011`
- Judge: `outputs.validate_output (evals/week7/outputs.py, week-6 contract judge)` - one function, both arms
- Arm A = single ReAct agent (week 7, unchanged). Arm B = orchestrator + `coverage/exclusions worker` + `adjuster-note summary worker`.

| Metric | Single agent (A) | Claims squad (B) | Winner |
| --- | ---: | ---: | --- |
| Pass rate (same judge, same 10 cases) | 100.0% (10/10) | 90.0% (9/10) | single |
| p50 latency | 57528.4 ms | 44998.1 ms | squad |
| p99 latency | 102438.2 ms | 167476.3 ms | single |
| Total tokens | 62,573 | 59,755 | squad |
| Cost per claim | $0.005602 | $0.006799 | single |

**Context re-send multiplier: 1.0x** (59,755 squad tokens / 62,573 single tokens).
**Largest single hand-off:** `orchestrator->coverage_worker` - 32,064 tokens = 53.7% of all squad-arm tokens (from `handoffs.log`).

### Hand-off totals (squad arm, from `handoffs.log`)

| Hand-off | Model calls | Prompt tokens (re-sent context) | Completion tokens | Total | Share of squad tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| `orchestrator->coverage_worker` | 29 | 29,672 | 2,392 | 32,064 | 53.7% |
| `orchestrator->summary_worker` | 18 | 12,774 | 1,974 | 14,748 | 24.7% |
| `orchestrator->synthesis` | 9 | 8,266 | 476 | 8,742 | 14.6% |
| `orchestrator->planner` | 10 | 2,735 | 1,466 | 4,201 | 7.0% |

### Per-claim results (same 10 cases)

| Claim | Expected status | A pass | A latency (ms) | A tokens | A cost | B pass | B latency (ms) | B tokens | B cost |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| W6-001 | approved | PASS | 5890.8 | 4,179 | 0.003855 | PASS | 18994.9 | 6,442 | 0.007131 |
| W6-002 | excluded | PASS | 68313.1 | 9,162 | 0.008027 | PASS | 74884.3 | 6,387 | 0.007113 |
| W6-003 | approved | PASS | 54048.8 | 5,763 | 0.005234 | PASS | 18858.5 | 6,501 | 0.00737 |
| W6-004 | excluded | PASS | 65875.4 | 6,404 | 0.005728 | PASS | 15945.7 | 4,703 | 0.005615 |
| W6-005 | approved | PASS | 5536.4 | 4,057 | 0.003726 | PASS | 73841.8 | 6,392 | 0.007469 |
| W6-006 | approved | PASS | 60277.4 | 9,046 | 0.007947 | PASS | 71001.3 | 6,578 | 0.00764 |
| W6-008 | excluded | PASS | 67860.7 | 6,474 | 0.005778 | PASS | 18928.8 | 6,460 | 0.007338 |
| W6-009 | approved | PASS | 54779.4 | 4,159 | 0.003839 | PASS | 74865.7 | 6,445 | 0.007329 |
| W6-010 | approved | PASS | 5669.4 | 4,099 | 0.003791 | PASS | 18433.8 | 6,146 | 0.006779 |
| W6-011 | excluded | PASS | 105813.2 | 9,230 | 0.008091 | FAIL | 176633.8 | 3,701 | 0.004206 |

Notes: p50/p99 use linear interpolation over the 10 per-claim latencies (p99 with n=10 sits between the 9th and 10th value - it is reported, not hidden). Latency is wall-clock for the whole claim, including the inter-call pace both arms pay against the rate-limited key.
