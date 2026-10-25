# 10-minute demo script

Fill every `<...>` from your own run's `reports/` files before presenting. Do not say a number that is not in a report.

## Part 1 (3 min): a real run
`python capstone/run.py triage --claim C-S1` (loss 2024-08-11, flood, engine ingress).
Point at, in order: the `check_coverage_in_force` call and its fact; `search_policy_wording` discovered over MCP (name it: it is not in the agent's source);
the citation **Exc-7.4, endorsement 2023, resolved against 2024-08-11**; deductible / flood sub-limit / policy limit as three figures; the referral ticket.
Then `python capstone/run.py trace <trace id>`: one id on the agent spans and the MCP server span.

## Part 2 (3 min): the failures
- The headline failure: `reports/suite_before.json` trace `<id>` where a 2024 loss was cited against 2025 wording. Frequency in the re-scored taxonomy: `<x%>` (`reports/EVAL_REPORT.md` section 3).
- The mode integration created: `<mode name>` (`created by integration` column), trace `<id>`.
- Two more weaknesses from `docs/WEAKNESSES.md` (retrieval miss on paraphrase C-S7; pattern-based redaction / phrase-list guard).

## Part 3 (2 min): the number that moved
Wrong-endorsement-version citation rate **`<a%>` (n=`<n>`) before -> `<b%>` (n=`<n>`) after** (EVAL_REPORT section 2).
Retrieval-only (no model): wrong version in top-3 100% -> 0%, straddle n=6 (EVAL_REPORT section 1).
Judge agreement on integrated outputs: `<k/n = x%>` against hand labels (section 4). Say it if it is under 85%.
Not measured at all: real policy wording; the suite uses synthetic fixtures (section 5).

## Part 4 (2 min): what's next
- Permanent guard: `python capstone/run.py loop` (six straddle cases red with the filter off, green with it on; also run by `unittest`).
- Cost per claim triaged: `<$>` (`reports/COST_10X.md`). At 18,000 FNOLs/week the first limit to name is `<resource, number>` from `run.py cost --tpm <your limit>`.
- Ranked next steps with cost: (1) measure with the hybrid ranker and fix the paraphrase miss, (2) hand-label and re-check judge agreement, (3) worker pool + queue in front of the agent.

## Hostile questions: where the answer lives
1. *11 Aug 2024 cited the 2025 exclusion; how many cases straddle a version and what do they score?* `EVAL_REPORT.md` section 3, slice `straddle` (n and `cited version in force %`), plus section 2.
2. *Does it ever say "covered"? Show the assertion.* `core/guard.py` (post-filter, code) and `checks/assertions.py::no_decision_words_delivered`; `model_wrote_no_decision_words` shows how often the model tried. No decision field exists in the contract.
3. *Deductible vs sub-limit vs policy limit.* Trace `<id>` of a `limits` slice case; assertion `money_trio` in `checks/assertions.py` compares all three to the schedule.
4. *Reproduce claim 4471-A in 18 months?* `python capstone/run.py trace <id> --replay`: stored wording version, chunk ids, model, prompt version, exact messages (redacted). Retrieval replays; the model's text is not guaranteed. Say that.
5. *What is in your logs and what did the MCP server hand over?* `tests/test_capstone.py::Pii`, assertion `trace_pii_clean`, and `reports/mcp_wire_capture.json` from the other squad's client run: clause wording only, no claimant data.
