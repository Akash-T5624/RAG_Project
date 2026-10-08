"""Slice the request log until the bad answer shows up.

The complaint you get from support never has a trace id: "it told a claimant
something was covered, sometime yesterday".  This tool turns that sentence into
narrowing filters and prints how many traces survive each one, so you can see
which slice did the work.

Every filter is optional; they compose left to right:

    python trace_search.py --since 2026-10-07 --until 2026-10-08
    python trace_search.py --since 2026-10-07 --verdict covered
    python trace_search.py --since 2026-10-07 --verdict covered --no-exclusion-cite
    python trace_search.py --input-type coverage --outlier 90
    python trace_search.py --show <trace_id>          # full trace, pretty printed

Slices
  --since/--until/--days   time window (UTC, date or full ISO)
  --endpoint               /chat, /chat/stream, batch      (input route)
  --input-type             coverage | payout | policy-term | claim-procedure | general
  --prompt-version         which pipeline produced it
  --verdict                covered | not_covered | refusal | unknown | empty
  --no-exclusion-cite      answer never mentions the exclusions wording
  --missing-exclusions     retrieval context never surfaced "Important Exclusions"
                           (needs the coverage_guard log field -- Week 11 bonus)
  --cost-outlier PCT       requests whose cost is at or above the Pth percentile
  --session                session/user id
  --grep TEXT              substring of question or answer (both indexed)
  --min-tokens N           generation token floor
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE / "traces"
JSONL = HERE / "trace.jsonl"

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def _parse_ts(value: str) -> datetime:
    value = value.strip()
    if len(value) == 10:            # YYYY-MM-DD
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_traces(directory: Path) -> list[dict]:
    traces = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                traces.append(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    return traces


def _created(trace: dict) -> datetime | None:
    raw = trace.get("created_at")
    if not raw:
        return None
    try:
        return _parse_ts(raw)
    except ValueError:
        return None


def _output_index(trace: dict) -> dict:
    return trace.get("output_index") or {}


def _verdict(trace: dict) -> str:
    return _output_index(trace).get("coverage_verdict", "n/a")


def _context_has_exclusions(trace: dict) -> bool | None:
    """True when the retrieved context carried the exclusions schedule.

    Returns None when the trace predates the coverage_guard field, so old
    logs are never mistaken for clean ones.
    """
    guard = (trace.get("coverage_guard") or {}).get("exclusions_in_context")
    return guard


def _context_text(trace: dict) -> str:
    """Everything that was actually handed to the model: the system message
    carries the assembled pages, so this is the context that decided the
    answer -- not the raw retrieval hits."""
    parts = []
    for msg in trace.get("llm_messages") or []:
        if msg.get("role") == "system":
            parts.append(msg.get("content") or "")
    return "\n".join(parts).lower()


def _cost(trace: dict) -> float:
    cost = trace.get("cost_usd") or {}
    if isinstance(cost, dict):
        return float(cost.get("total", 0.0) or 0.0)
    return float(cost or 0.0)


def _tokens(trace: dict) -> int:
    return int((trace.get("usage") or {}).get("total_tokens", 0) or 0)


def _fmt_row(t: dict, width: int = 44) -> str:
    created = t.get("created_at", "?")[:19]
    q = (t.get("question") or "").replace("\n", " ")
    return (f"{t.get('trace_id', '?')[:8]}  {created}  {t.get('input_type', 'n/a'):<14} "
            f"{_verdict(t):<12} {_cost(t):.6f}  {t.get('latency_ms', 0):>8.1f}ms "
            f"{t.get('prompt_version', 'n/a'):<24} {q[:width]}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="trace directory")
    p.add_argument("--since", help="YYYY-MM-DD or full ISO (UTC, inclusive)")
    p.add_argument("--until", help="YYYY-MM-DD or full ISO (UTC, exclusive)")
    p.add_argument("--days", type=int, help="last N days (shortcut for --since)")
    p.add_argument("--endpoint")
    p.add_argument("--input-type")
    p.add_argument("--prompt-version")
    p.add_argument("--verdict", choices=["covered", "not_covered", "refusal",
                                         "unknown", "empty", "n/a"])
    p.add_argument("--no-exclusion-cite", action="store_true",
                   help="answer text never mentions exclusions wording")
    p.add_argument("--context-missing", metavar="TEXT",
                   help="TEXT never appeared in the context sent to the model "
                        "(e.g. --context-missing \"Important Exclusions\")")
    p.add_argument("--context-has", metavar="TEXT",
                   help="TEXT did appear in the context sent to the model")
    p.add_argument("--missing-exclusions", action="store_true",
                   help="retrieval context had no exclusions schedule "
                        "(needs coverage_guard field)")
    p.add_argument("--cost-outlier", type=float, metavar="PCT",
                   help="keep requests at/above this cost percentile (e.g. 90)")
    p.add_argument("--min-tokens", type=int)
    p.add_argument("--session")
    p.add_argument("--grep", help="substring of question OR answer")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true", help="one JSON object per line")
    p.add_argument("--show", help="print one full trace by id prefix and exit")
    args = p.parse_args(argv)

    traces = load_traces(Path(args.dir))
    if not traces and JSONL.exists():
        traces = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines()
                  if l.strip()]
    total = len(traces)
    print(f"loaded {total} traces from {args.dir}")

    if args.show:
        matches = [t for t in traces if str(t.get("trace_id", "")).startswith(args.show)]
        if not matches:
            print(f"no trace starting with {args.show}")
            return 1
        print(json.dumps(matches[0], indent=2, ensure_ascii=False))
        return 0

    steps: list[tuple[str, object]] = []

    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        steps.append(("days", lambda t, s=since: (_created(t) or s) >= s))
    if args.since:
        s = _parse_ts(args.since)
        steps.append((f"since {args.since}", lambda t, s=s: (_created(t) or s) >= s))
    if args.until:
        u = _parse_ts(args.until)
        steps.append((f"until {args.until}", lambda t, u=u: (_created(t) or u) < u))
    if args.endpoint:
        steps.append((f"endpoint={args.endpoint}",
                      lambda t, v=args.endpoint: t.get("endpoint") == v))
    if args.input_type:
        steps.append((f"input_type={args.input_type}",
                      lambda t, v=args.input_type: t.get("input_type") == v))
    if args.prompt_version:
        steps.append((f"prompt_version={args.prompt_version}",
                      lambda t, v=args.prompt_version: t.get("prompt_version") == v))
    if args.session:
        steps.append((f"session={args.session}",
                      lambda t, v=args.session: t.get("session_id") == v))
    if args.verdict:
        steps.append((f"verdict={args.verdict}",
                      lambda t, v=args.verdict: _verdict(t) == v))
    if args.no_exclusion_cite:
        steps.append(("no exclusion cite",
                      lambda t: not _output_index(t).get("exclusion_phrases")))
    if args.context_missing:
        needle = args.context_missing.lower()
        steps.append((f"context missing {args.context_missing!r}",
                      lambda t, n=needle: n not in _context_text(t)))
    if args.context_has:
        needle = args.context_has.lower()
        steps.append((f"context has {args.context_has!r}",
                      lambda t, n=needle: n in _context_text(t)))
    if args.missing_exclusions:
        steps.append(("retrieval context missed exclusions",
                      lambda t: _context_has_exclusions(t) is False))
    if args.min_tokens:
        steps.append((f"tokens>={args.min_tokens}",
                      lambda t, n=args.min_tokens: _tokens(t) >= n))
    if args.grep:
        needle = args.grep.lower()
        steps.append((f"text~{args.grep!r}",
                      lambda t, n=needle: n in (t.get("question") or "").lower()
                      or n in (t.get("raw_output") or "").lower()))

    kept = traces
    print(f"{'slice':<44}{'kept':>6}")
    print("-" * 50)
    for label, pred in steps:
        kept = [t for t in kept if pred(t)]
        print(f"{label:<44}{len(kept):>6}")

    if args.cost_outlier is not None:
        costs = sorted(_cost(t) for t in kept)
        if costs:
            idx = min(len(costs) - 1,
                      int(round(args.cost_outlier / 100.0 * (len(costs) - 1))))
            floor = costs[idx]
            kept = [t for t in kept if _cost(t) >= floor]
            print(f"{'cost>=' + format(floor, '.6f') + f' (p{args.cost_outlier:g})':<44}"
                  f"{len(kept):>6}")

    kept.sort(key=lambda t: t.get("created_at") or "")
    print("-" * 50)
    print(f"{'MATCHES':<44}{len(kept):>6}\n")

    if args.json:
        for t in kept:
            print(json.dumps(t, ensure_ascii=False))
        return 0

    if not kept:
        return 1
    print(f"{'trace':<9}{'created_at (UTC)':<21}{'input_type':<15}{'verdict':<13}"
          f"{'cost_usd':<10}{'latency':<12}{'prompt_version':<25}question")
    print("-" * 140)
    for t in kept[:args.limit]:
        print(_fmt_row(t))
    if len(kept) > args.limit:
        print(f"... {len(kept) - args.limit} more (raise --limit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
