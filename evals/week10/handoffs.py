"""Per-hand-off token accounting for the claims squad.

Every model call made by the orchestrator arm is a hand-off: the manager
re-sends context to a planner, to a specialist, or to its own synthesis step.
One line per model call, because context is re-sent per call - that is exactly
where the multiplier comes from.

Line format (space separated ``key=value``):

    ts=<iso> case=W6-002 hop=orchestrator->coverage_worker call=1 status=200 \\
        prompt_tokens=1412 completion_tokens=96 total_tokens=1508 \\
        cost_usd=0.0015 latency_ms=4521.0 detail="..."

``prompt_tokens`` is the re-sent context (the bill the hand-off charges);
``completion_tokens`` is what came back.  Failed dispatches (HTTP 500) are
logged with 0 billed tokens and ``status=HTTP500`` so the retry is visible in
the same log the multiplier is computed from.
"""

import json
from datetime import datetime, timezone

HEADER = """# Week 10 Module 5 - hand-off log (claims squad arm only)
# One line per model call made by the orchestrator arm; a single hand-off may
# span several model calls (tool loop), so lines are grouped by `hop=`.
# hop names:
#   orchestrator->planner            manager decomposes the claim question
#   orchestrator->coverage_worker    dispatch to the coverage/exclusions specialist
#   orchestrator->summary_worker     dispatch to the adjuster-note summary specialist
#   orchestrator->synthesis          manager re-sends both worker findings to decide
# The single-agent arm makes no hand-offs; its tokens are in race_table.md.
"""

HOP_PLANNER = "orchestrator->planner"
HOP_COVERAGE = "orchestrator->coverage_worker"
HOP_SUMMARY = "orchestrator->summary_worker"
HOP_SYNTHESIS = "orchestrator->synthesis"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_call(case, hop, call_index, status, prompt_tokens, completion_tokens,
              cost, latency_ms, detail=""):
    return {
        "ts": now_iso(),
        "case": case,
        "hop": hop,
        "call": call_index,
        "status": status,
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(prompt_tokens) + int(completion_tokens),
        "cost_usd": round(float(cost), 6),
        "latency_ms": round(float(latency_ms), 1),
        "detail": detail,
    }


def format_line(rec):
    fields = dict(rec)
    fields["detail"] = json.dumps(rec.get("detail", ""), ensure_ascii=False)
    return (
        "ts={ts} case={case} hop={hop} call={call} status={status} "
        "prompt_tokens={prompt_tokens} completion_tokens={completion_tokens} "
        "total_tokens={total_tokens} cost_usd={cost_usd:.6f} "
        "latency_ms={latency_ms:.1f} detail={detail}"
    ).format(**fields)


def parse_line(line):
    """Parse one log line back into a record (log is the source of truth)."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    out = {}
    for token in line.split(" "):
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        out[k] = v
    if "hop" not in out:
        return None
    detail = out.get("detail", '""')
    try:
        detail = json.loads(detail)
    except json.JSONDecodeError:
        pass
    return {
        "ts": out.get("ts", ""),
        "case": out.get("case", ""),
        "hop": out["hop"],
        "call": int(out.get("call", 0)),
        "status": out.get("status", ""),
        "prompt_tokens": int(out.get("prompt_tokens", 0)),
        "completion_tokens": int(out.get("completion_tokens", 0)),
        "total_tokens": int(out.get("total_tokens", 0)),
        "cost_usd": float(out.get("cost_usd", 0.0)),
        "latency_ms": float(out.get("latency_ms", 0.0)),
        "detail": detail,
    }


def write_log(path, records):
    """Write the full log (header + every hand-off, in order)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [HEADER.rstrip("\n")]
    lines.extend(format_line(r) for r in records)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_log(path):
    if not path.exists():
        return []
    recs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = parse_line(line)
        if rec:
            recs.append(rec)
    return recs


def hop_totals(records):
    """Aggregate token totals per named hand-off."""
    totals = {}
    for r in records:
        hop = r["hop"]
        bucket = totals.setdefault(hop, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "cost_usd": 0.0, "cases": set(),
        })
        bucket["calls"] += 1
        bucket["prompt_tokens"] += r["prompt_tokens"]
        bucket["completion_tokens"] += r["completion_tokens"]
        bucket["total_tokens"] += r["total_tokens"]
        bucket["cost_usd"] += r["cost_usd"]
        bucket["cases"].add(r["case"])
    for bucket in totals.values():
        bucket["cases"] = sorted(bucket["cases"])
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    return totals


def dominant_hop(records, denominator_tokens):
    """The single hand-off with the largest share of the denominator tokens.

    Returns (hop, share_pct, hop_total_tokens) or (None, 0.0, 0).
    """
    totals = hop_totals(records)
    if not totals or not denominator_tokens:
        return None, 0.0, 0
    hop, bucket = max(totals.items(), key=lambda kv: kv[1]["total_tokens"])
    share = bucket["total_tokens"] / float(denominator_tokens) * 100.0
    return hop, round(share, 1), bucket["total_tokens"]
