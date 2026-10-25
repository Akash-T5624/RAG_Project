# Architecture (one page)

```
 FNOL in (claim id or free text)                         run.py  (single entry point)
        |  mint ONE trace_id  (core/tracing.Trace)
        v
 +---------------------------------------------------------------+
 | agent/loop.py   hand-built JSON ReAct loop (Week 7)           |
 |   4 budgets: iterations, tokens, cost, wall-clock             |
 |   MODEL CALL x N  -> Groq (core/llm.py)    $ <- main spend    |
 +--------+---------------------------+--------------------------+
          | local tools (in-process)  | MCP tool discovered by tools/list at runtime
          v                           v
  get_claim                  mcp_layer/mcp_client.py  --stdio/JSON-RPC-->  mcp_layer/policy_server.py
  lookup_policy                 adds trace_id arg                              |  search_policy_wording(query, product, loss_date, trace_id)
  check_coverage_in_force       enforces loss_date+product                     |  core/retriever.py
  refer_to_adjuster             from the claim record                          |    1. DATE FILTER in code (before ranking)
          |                           ^                                        |    2. Week 3-4 hybrid rank: FAISS + BM25 + RRF + cross-encoder
          |                           |  echoes trace_id + server span         |       (BM25 fallback if faiss/torch missing; `ranker` says which)
          |                           +----------------------------------------+    3. PII redaction on everything returned (core/pii.py)
          v
 assemble(): citations resolved BY CODE from what retrieval returned
             (clause, endorsement version, in-force range, loss date resolved against, page, anchor, quote)
 guard.sanitize_output(): removes decision words from model-authored fields (core/guard.py)
          |
          v
 delivered output -> adjuster briefing + referral ticket          (no decision field exists)
          |
          v
 core/tracing.finalize -> backend/trace_log.build_trace (redact before write) -> reports/traces/<trace_id>.json
        spans: agent.llm#n (tokens, $), tool.*, mcp.client.*, mcp.server.*   one id across both hops
```

Where the model is called: only `agent.llm#n`. The judge (`checks/judge.py`) is a second model use, offline from triage.
Where the money goes: the agent's model calls (tokens x list price in `backend/pricing.py`). Retrieval, tools and the MCP server cost $0 and show up as latency.
What is enforced in code, not in the prompt: the date filter, the loss-date and product arguments, the citation resolution, the decision-word guard, the redaction, the required steps (in-force check, wording search, referral) before a final answer is accepted.

Eval side (all under `checks/`, one command `run.py eval`): `assertions.py` (before any judge) -> `taxonomy.py` -> `trajectory.py` -> `judge.py` -> `report.py`.
