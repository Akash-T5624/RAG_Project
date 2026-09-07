## Week 7 Module 4 - Final comparison: Agent vs Fixed Workflow

Model: `qwen/qwen3.8-27b`  ·  Pricing: input $0.8/1M, output $4.0/1M (Groq list price)
Evaluation data: `evals/week6/eval_set.jsonl` (27 claims); race set selects 10 by the documented deterministic rule.

| Metric | Agent | Fixed Workflow |
| --- | ---: | ---: |
| Pass rate | 100.0% | 100.0% |
| p50 latency (ms) | 31715.3 | 2.7 |
| Total tokens | 46466 | 0 |
| Cost per claim ($) | 0.004273 | 0.0 |

### Per-claim results

| Claim ID | Agent Pass | Agent Latency (ms) | Agent Tokens | Agent Cost ($) | Workflow Pass | Workflow Latency (ms) | Workflow Tokens | Workflow Cost ($) |
| --- | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: |
| W6-001 | PASS | 5884.5 | 3623 | 0.003404 | PASS | 2.4 | 0 | 0.0 |
| W6-002 | PASS | 55834.4 | 5715 | 0.005186 | PASS | 2.7 | 0 | 0.0 |
| W6-003 | PASS | 5710.9 | 3559 | 0.003353 | PASS | 4.0 | 0 | 0.0 |
| W6-004 | PASS | 57206.8 | 5671 | 0.005145 | PASS | 2.7 | 0 | 0.0 |
| W6-005 | PASS | 5555.0 | 3501 | 0.003274 | PASS | 2.1 | 0 | 0.0 |
| W6-006 | PASS | 61122.3 | 5757 | 0.005217 | PASS | 2.2 | 0 | 0.0 |
| W6-008 | PASS | 65863.4 | 5741 | 0.005194 | PASS | 3.7 | 0 | 0.0 |
| W6-009 | PASS | 5792.4 | 3607 | 0.003398 | PASS | 2.8 | 0 | 0.0 |
| W6-010 | PASS | 55048.4 | 3543 | 0.00334 | PASS | 3.3 | 0 | 0.0 |
| W6-011 | PASS | 8382.2 | 5749 | 0.005217 | PASS | 2.6 | 0 | 0.0 |
