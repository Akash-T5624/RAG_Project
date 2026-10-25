"""MCP server exposing ONE real capability: loss-date-scoped policy wording search.

No model client lives here. The tool wraps core/retriever (date filter enforced in code), strips PII
from everything it returns and from what it logs, echoes the caller's trace id, and reports its own
span so the client can merge it into the end-to-end trace.

    python capstone/mcp_layer/policy_server.py            # stdio (spawned by a client)
    python capstone/mcp_layer/policy_server.py --http     # http://127.0.0.1:8765/mcp for another squad's client
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastmcp import FastMCP  # noqa: E402

from capstone.core.paths import TRACES  # noqa: E402
from capstone.core.pii import redact, redact_obj  # noqa: E402
from capstone.core.retriever import search_wording  # noqa: E402

mcp = FastMCP("Policy wording search")


@mcp.tool
def search_policy_wording(query: str, product: str, loss_date: str, trace_id: str = "") -> dict:
    """Search the policy wording that was IN FORCE on the loss date.

    Use this for any exclusion, sub-limit or deductible wording question about a claim. Pass a short
    topic as `query`, the claim's `product` ("motor" or "household") and its `loss_date` as YYYY-MM-DD.
    Every hit carries clause_id and endorsement_version: cite both. Wording not in force on that date
    is never returned. If `status` is NO_WORDING_IN_FORCE or BAD_DATE, do not infer wording: refer to
    an adjuster. This tool is read-only; it never decides a claim and never returns claimant data.
    """
    t0 = time.perf_counter()
    # CAPSTONE_DATE_FILTER=0 exists only to reproduce the pre-fix behaviour for the before/after measurement.
    result = redact_obj(search_wording(query, product, loss_date, date_filter=os.getenv("CAPSTONE_DATE_FILTER", "1") != "0"))
    duration_ms = (time.perf_counter() - t0) * 1000.0
    result["_server"] = {"trace_id": trace_id, "duration_ms": round(duration_ms, 1), "tool": "search_policy_wording",
                         "ranker": result.get("ranker"), "status": result.get("status")}
    TRACES.mkdir(parents=True, exist_ok=True)
    line = {"trace_id": trace_id, "tool": "search_policy_wording", "duration_ms": round(duration_ms, 1),
            "args": {"query": redact(query), "product": product, "loss_date": loss_date},
            "status": result.get("status"), "returned": [h["chunk_id"] for h in result.get("hits", [])]}
    with open(TRACES / "mcp_server.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="http", host="127.0.0.1", port=8765)
    else:
        mcp.run(transport="stdio")
