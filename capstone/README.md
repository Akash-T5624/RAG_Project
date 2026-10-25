# Claims Triage & Coverage Assistant (Week 12 capstone, Variant D)

Takes a motor or household FNOL, checks the policy was in force on the loss date, retrieves the policy wording that applied **on that date**, cites it by clause and
endorsement version, reports deductible, sub-limit and policy limit as three separate numbers, and refers the claim to an adjuster. It never states a coverage decision.

The endorsement wording, policies and claims are **synthetic** test data authored for this project (`data/build_fixtures.py`).

## Prerequisites
- Python 3.11 or 3.12 recommended (the repo was developed on newer Python; the heavy packages may lack wheels on very new versions)
- A Groq API key

## Setup (from the repo root)
```powershell
python -m pip install -r capstone/requirements.txt
copy backend\.env.example backend\.env      # then set GROQ_API_KEY=... in backend\.env
```

## Run
```powershell
python capstone/run.py all                  # one command: fixtures, unit tests, loss-date retrieval check, closed loop, then the live suite
python capstone/run.py triage --claim C-S1  # one claim end to end; prints the tool calls, the briefing and the trace id
python capstone/run.py trace <trace_id>     # spans with cost and latency per hop; add --replay to replay retrieval from the trace
```
Without `GROQ_API_KEY`, `all` still runs everything that needs no model and marks the rest NOT RUN. Without faiss/torch the retriever uses BM25 and says so (`ranker`).

## What you should see for `triage --claim C-S1` (loss 2024-08-11)
`get_claim -> check_coverage_in_force -> lookup_policy -> search_policy_wording -> refer_to_adjuster`, then wording cited as **Exc-7.4, endorsement 2023**
(never 2025), deductible 2500 / flood sub-limit 50000 / policy limit 400000 as separate figures, and a referral ticket.

## Other commands
`eval [--before]` (live suite; `--before` runs with the date filter off), `label-sheet` then `judge` (judge agreement needs your hand labels in `data/hand_labels.json`),
`report`, `cost --tpm <limit>`, `loop`, `serve-mcp [--http]`, `test`.
For another squad: `python capstone/mcp_layer/fresh_eyes_client.py` (imports nothing from this repo).

## Where things are
`core/` retriever, date filter, guard, PII, tracing | `agent/` loop and tools | `mcp_layer/` server and client | `checks/` eval, taxonomy, trajectory, judge, reports |
`docs/` seams, weaknesses, architecture, demo script, fresh-eyes note | `reports/` generated output (traces, EVAL_REPORT.md, COST_10X.md)
