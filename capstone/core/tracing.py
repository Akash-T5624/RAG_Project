"""One trace id from FNOL in to referral out, across the agent hop and the MCP hop.

The id is minted once at entry (Trace()), handed to every tool call, sent to the MCP server as the
`trace_id` argument, echoed back by the server together with its own span, and merged here. The
finished record is built by backend/trace_log.build_trace (redaction before write, cost block,
spans), then stamped with OUR id and the capstone fields needed to replay and defend the answer.
"""

import json
import threading
import time
import uuid
from contextlib import contextmanager

import capstone.core.pii  # noqa: F401  (installs the extended redactor before anything is written)
import trace_log
from capstone.core.paths import TRACES

_JSONL_LOCK = threading.Lock()


class Trace:
    def __init__(self, claim_id, trace_id=None):
        self.trace_id = trace_id or str(uuid.uuid4())
        self.claim_id = claim_id
        self.t0 = time.perf_counter()
        self.spans = []
        self.integrity = {}

    def now_ms(self):
        return (time.perf_counter() - self.t0) * 1000.0

    @contextmanager
    def span(self, name, hop="agent", **detail):
        rec = {"name": name, "start_ms": self.now_ms(), "duration_ms": 0.0, "status": "ok",
               "tokens": None, "cost_usd": 0.0, "detail": {"hop": hop, **detail}}
        t = time.perf_counter()
        try:
            yield rec
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            rec["status"] = "error"
            rec["detail"]["error"] = str(exc)[:200]
            raise
        finally:
            rec["duration_ms"] = (time.perf_counter() - t) * 1000.0
            self.spans.append(rec)

    def add_remote_span(self, name, server_info, start_ms, hop="mcp"):
        """Merge the span the MCP server reported for this call, anchored inside the client span."""
        self.spans.append({"name": name, "start_ms": start_ms, "status": "ok", "tokens": None,
                           "duration_ms": float(server_info.get("duration_ms", 0.0)), "cost_usd": 0.0,
                           "detail": {"hop": hop, **{k: v for k, v in server_info.items() if k != "duration_ms"}}})

    def total_cost(self):
        return sum(s.get("cost_usd") or 0.0 for s in self.spans)

    def finalize(self, *, question, llm_messages, hits, raw_output, model, usage, extra, error=None):
        """Build + persist the trace. `hits` are wording hits seen by the agent (any hop)."""
        total_ms = self.now_ms()
        results = [{"rank": i + 1, "chunk_id": h["chunk_id"], "chunk_index": i, "chunk": h.get("text", ""),
                    "metadata": {"page_number": h.get("page")}, "score": h.get("score", 0.0)}
                   for i, h in enumerate(hits)]
        gen_cost = sum(s["cost_usd"] or 0.0 for s in self.spans if s["name"].startswith("agent.llm"))
        trace = trace_log.build_trace(
            endpoint="capstone/triage", session_id=self.claim_id, doc_id="capstone-wording-store",
            prompt_version=extra.get("prompt_version", "capstone-v1"), question=question,
            llm_messages=llm_messages, retrieval_results=results, model=model, temperature=0.0, stream=False,
            raw_output=raw_output, latency_ms=total_ms, error=error, spans=self.spans, usage=usage,
            cost={"retrieval": 0.0, "generation": gen_cost, "tools": 0.0, "total": round(self.total_cost(), 8)})
        trace["trace_id"] = self.trace_id
        trace["capstone"] = capstone.core.pii.redact_obj({
            **extra,
            "integrity": self.integrity,
            "wording": [{"chunk_id": h["chunk_id"], "clause_id": h["clause_id"],
                         "endorsement_version": h["endorsement_version"],
                         "effective_from": h["effective_from"], "effective_to": h["effective_to"]} for h in hits],
        })
        save(trace)
        return trace


def save(trace):
    path = trace_log.save_trace(trace, directory=TRACES)
    with _JSONL_LOCK:
        with open(TRACES.parent / "trace.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(trace, ensure_ascii=False) + "\n")
    return path


def load(trace_id):
    return trace_log.load_trace(trace_id, directory=TRACES)
