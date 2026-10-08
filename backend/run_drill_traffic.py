"""Replay a day of support traffic through the app, with backdated timestamps.

Production never runs this.  It exists so that the trace log contains real
traffic for the Week 11 support drill to search: every request goes through the
real endpoint (real retrieval, real Groq call, real trace write); only the
`created_at` written into the trace is shifted into the past, so the log looks
like "yesterday".  The drill harness discloses this in drill.md.

Example (drill traffic for yesterday):

    python run_drill_traffic.py --questions ..\evals\week11\traffic_questions.jsonl
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = (BACKEND.parent / "evals" / "week11" / "traffic_questions.jsonl")
BIKE_DOC = "f833ddcd-81b2-4260-b46c-d59fb3bca267"  # sample two-wheeler policy

import app as appmod  # noqa: E402  (loads models + index once)
import trace_log  # noqa: E402


def parse_ts(value: str) -> datetime:
    if value == "yesterday":
        # "Yesterday" means yesterday where the squad sits, not in UTC.
        local = datetime.now().astimezone()
        yesterday = (local - timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)
        return yesterday.astimezone(timezone.utc)
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_questions(path: Path, repeats: int) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("//"):
            items.append(json.loads(line))
    return items * max(1, repeats)


def already_written(before: set[str]) -> str | None:
    """Newest trace created since the snapshot (by file mtime -- trace ids are
    random, so they cannot be ordered).  Retries that failed have written their
    own error trace; the newest one belongs to the last attempt."""
    new = set(trace_log.list_trace_ids()) - before
    if not new:
        return None
    return max(new, key=lambda tid: trace_log.load_trace_path(tid).stat().st_mtime)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    p.add_argument("--doc-id", default=BIKE_DOC)
    p.add_argument("--start", default="yesterday",
                   help='"yesterday" or an ISO timestamp (UTC)')
    p.add_argument("--span-minutes", type=int, default=480,
                   help="how much clock time the traffic is spread over")
    p.add_argument("--sleep", type=float, default=6.0,
                   help="pause between requests (Groq caps tokens/minute)")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--stream-fraction", type=float, default=0.25,
                   help="share of requests sent to /chat/stream")
    p.add_argument("--sessions", type=int, default=6)
    p.add_argument("--max-retries", type=int, default=4)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    questions = load_questions(Path(args.questions), args.repeats)
    rng = random.Random(args.seed)
    rng.shuffle(questions)

    window_start = parse_ts(args.start)
    step = timedelta(minutes=args.span_minutes / max(1, len(questions)))
    now = datetime.now(timezone.utc)
    plan = []
    for i, q in enumerate(questions):
        played_at = window_start + step * i
        use_stream = (i % 10) < int(args.stream_fraction * 10)
        plan.append({
            "item": q,
            "played_at": played_at,
            "endpoint": "/chat/stream" if use_stream else "/chat",
            "session_id": f"drill-sess-{(i % args.sessions) + 1:02d}",
            "offset_sec": (played_at - now).total_seconds(),
        })

    print(f"planned {len(plan)} requests | doc {args.doc_id[:8]} | "
          f"window {window_start.isoformat()} .. "
          f"{(window_start + step * len(plan)).isoformat()} | "
          f"stream fraction {args.stream_fraction:.2f}")
    if args.dry_run:
        for row in plan[:5]:
            print("  ", row["endpoint"], row["item"]["id"],
                  row["played_at"].isoformat(), row["item"]["question"][:60])
        return 0

    from fastapi.testclient import TestClient
    client = TestClient(appmod.app)

    ok = failed = 0
    total_cost = 0.0
    total_tokens = 0
    t_start = time.perf_counter()

    for i, row in enumerate(plan, 1):
        item = row["item"]
        trace_log.set_time_offset(row["offset_sec"])
        before = set(trace_log.list_trace_ids())
        payload = {
            "question": item["question"],
            "doc_id": args.doc_id,
            "session_id": row["session_id"],
        }
        status, detail, elapsed = None, "", 0.0
        for attempt in range(args.max_retries + 1):
            t0 = time.perf_counter()
            try:
                resp = client.post(row["endpoint"], json=payload)
            except Exception as exc:  # server-side failure
                resp, detail = None, str(exc)
            elapsed = time.perf_counter() - t0
            status = resp.status_code if resp is not None else 0
            rate_limited = status in (429, 502, 503) or (
                resp is not None and "429" in resp.text)
            if status == 200 or not rate_limited or attempt == args.max_retries:
                break
            wait = 20 * (attempt + 1)
            print(f"      rate limited -> sleeping {wait}s "
                  f"(attempt {attempt + 1})")
            time.sleep(wait)
        trace_log.set_time_offset(0.0)

        trace_id = already_written(before)
        cost = 0.0
        tokens = 0
        if trace_id:
            tr = trace_log.load_trace(trace_id)
            cost = float((tr.get("cost_usd") or {}).get("total", 0.0))
            tokens = int((tr.get("usage") or {}).get("total_tokens", 0))
            total_cost += cost
            total_tokens += tokens
        if status == 200:
            ok += 1
        else:
            failed += 1
            detail = detail or (resp.text[:160] if resp is not None else "")

        print(f"[{i:>3}/{len(plan)}] {row['endpoint']:<13} {item['id']:<5} "
              f"http={status} {elapsed:5.1f}s cost=${cost:.6f} "
              f"tokens={tokens:<5} {(trace_id or '')[:8]}  "
              f"{item['question'][:52]}")

        if i < len(plan):
            time.sleep(args.sleep)

    wall = time.perf_counter() - t_start
    print("\n" + "=" * 70)
    print(f"sent={len(plan)} ok={ok} failed={failed} wall={wall / 60:.1f} min "
          f"cost=${total_cost:.6f} tokens={total_tokens} "
          f"avg_cost/query=${(total_cost / max(1, ok)):.6f}")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
