# Seam list: where the parts disagreed when joined

Written 2026-10-25 while integrating (Claude-assisted), from reading the Week 3-11 code and building the joins.
The body below is not edited after it is written. Only the STATUS table at the bottom changes.
Evidence for the first seven is the file and line named; the rest are in the traces/tests named.

| # | Seam | Where | Why it hurts |
|---|------|-------|--------------|
| S1 | Two retrievers: the Week 7 agent searches with a BM25-only copy over a pickle, the Week 3-4 app uses FAISS+BM25+RRF+cross-encoder | `evals/week7/policy.py` vs `backend/retrieval.py` | The agent was never using the RAG pipeline that was evaluated |
| S2 | `load_corpus()` reads `vectors.index` / `chunks.pkl` relative to the current directory | `backend/retrieval.py:66-76` | Works only when launched from `backend/` |
| S3 | Flat imports with `sys.path` hacks (`config`, `tools`, `claims`, `llm` collide by name across weeks) | `evals/week7/*`, `week8/trajectory_config.py`, `week10/*` | Cannot import two weeks in one process safely |
| S4 | Output contract carries `status: approved/excluded/rejected` and `payout` | `evals/week7/agent.py` FINAL_CONTRACT | Violates the compliance rule: nothing automated states a coverage decision |
| S5 | MCP policy server serves 3 hardcoded passages, no product or date arguments | `week9_mcp/policy_server.py` | Not a genuine capability; cannot be loss-date scoped |
| S6 | Claims-system MCP server returns `claimant` and adjuster names in clear text | `week9_mcp/claims_system_server.py` | A client from another squad would receive PII |
| S7 | The agent loop never calls MCP: the Week 9 host "stops at the MCP boundary" | `week9_mcp/mcp_agent.py` docstring | MCP was exposed but not consumed |
| S8 | `build_trace` mints a new uuid on every call | `backend/trace_log.py:273` | No id can survive the agent hop and the MCP hop |
| S9 | Redaction covers claim refs, labelled/titled names, long ids; not addresses or injuries | `backend/trace_log.py:46-55` | Brief's PII line (names, addresses, injuries) not held |
| S10 | Free-text fields added outside `build_trace` are not redacted | new `capstone` trace block | Model-written search queries could carry PII to disk |
| S11 | Week 8 expected sequences forbid `escalate_claim`; Week 6 assertions know only claim number, date, excess, exclusion id | `evals/week8/expected_sequences.py`, `evals/week6/assertions.py` | Referral is now the required outcome; nothing checks version, loss-date range, money trio or decision words |
| S12 | Week 6 judge criterion rewards an "Approve" recommendation; judge model (`GROQ_MODEL`) differs from the agent model | `evals/week6/judge_v2.txt` | Its 85% agreement does not transfer to briefings that must not decide |
| S13 | LLM-chosen date strings vs the retriever's date parser | agent <-> MCP arguments | A bad or reformatted date silently changes which wording applies |
| S14 | Week 7 client's 15 s read timeout vs a cold embedding-model load in the server | `week9_mcp/mcp_agent.py:81` | First MCP call can time out |
| S15 | `backend/indices/` is gitignored; hybrid ranker needs faiss/torch | `.gitignore`, `backend/retrieval.py` imports | A fresh clone has neither the index nor the packages |
| S16 | No corpus for the brief: one policy PDF, no endorsement sets, no in-force table, 26 claims | repo | Real 2023/2025 wording cannot be tested without authored fixtures |
| S17 | Rate-limited shared Groq key; Week 7 paced calls with `AGENT_CALL_PACE_S`, the capstone loop does not | `evals/week7/config.py` | Live suite may stall on 429s |

## STATUS (the only part that is updated)

| # | Status | How |
|---|--------|-----|
| S1 | fixed | agent uses `capstone/core/retriever.py`, which calls `hybrid_search_per_doc` |
| S2 | accepted | capstone never calls `load_corpus()`; it passes an explicit in-memory index. Backend module unchanged |
| S3 | accepted | capstone code is namespaced (`capstone.*`); week7 `llm` is still imported flat via `core/paths.ensure_paths()` |
| S4 | fixed | new contract in `agent/loop.py`: facts, citations, referral; no decision field |
| S5 | fixed | `mcp_layer/policy_server.py` wraps the real retriever with `(query, product, loss_date)` |
| S6 | fixed | that server is not mounted; the capstone server returns clause wording only, redacted |
| S7 | fixed | `mcp_layer/mcp_client.py`; `tests/test_capstone.py::Discovery` fails if the tool name is hardcoded |
| S8 | fixed | `core/tracing.Trace` owns the id; server echoes it; id is checked on every MCP call |
| S9 | fixed | `core/pii.py`, patched into `trace_log.redact_text` (a monkeypatch: accepted risk); bare names still not caught (see WEAKNESSES) |
| S10 | fixed | `redact_obj` over the capstone block in `tracing.finalize` |
| S11 | fixed | `checks/assertions.py`, `checks/trajectory.py` |
| S12 | fixed (partly) | new prompt `data/judge_integrated_v1.txt`; agreement is NOT MEASURED until you write `data/hand_labels.json` |
| S13 | fixed | strict `core/dates.py`; the dispatcher enforces `loss_date` and `product` from the claim record and logs any override as `mcp-arg-drift` |
| S14 | fixed | subclass with a 180 s read timeout |
| S15 | accepted | BM25 fallback is automatic and recorded per result; install `requirements.txt` for the hybrid ranker |
| S16 | accepted | SYNTHETIC fixtures, labelled; n is reported, not 500 |
| S17 | ran-out-of-time | not addressed; live runs retry on 429 via the Week 7 client |
