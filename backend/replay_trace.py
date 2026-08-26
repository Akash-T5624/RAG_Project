import argparse
import json
import os
import sys
from pathlib import Path

import requests

from trace_log import load_trace, TRACE_DIR, DEMO_TRACE_DIR

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

REPLAY_FIELDS = [
    "prompt_version",
    "question",
    "llm_messages",
    "model",
    "temperature",
    "raw_output",
]


def load_env_file(env_path=".env"):
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--demo", action="store_true",
                        help="Look in traces_demo/ instead of traces/")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not write evals/replay_evidence.md")
    args = parser.parse_args()

    load_env_file()
    directory = DEMO_TRACE_DIR if args.demo else TRACE_DIR

    try:
        trace = load_trace(args.trace_id, directory)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))

    # --- Replayability check -------------------------------------------
    missing = [f for f in REPLAY_FIELDS if f not in trace or trace[f] is None]
    print("=" * 60)
    print(f"REPLAY {trace['trace_id']}")
    print("=" * 60)
    if missing:
        print(f"MISSING FIELDS (could not reconstruct): {', '.join(missing)}")
    else:
        print("All replay fields present: "
              + ", ".join(REPLAY_FIELDS))

    api_key = os.getenv("GROQ_API_KEY")
    replayed = None
    error_detail = None
    if missing:
        error_detail = "trace missing fields: " + ", ".join(missing)
    elif not api_key or api_key == "paste_your_groq_api_key_here":
        error_detail = "GROQ_API_KEY not configured; cannot call Groq."
    else:
        try:
            response = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": trace["model"],
                    "messages": trace["llm_messages"],
                    "temperature": trace["temperature"],
                },
                timeout=60,
            )
            response.raise_for_status()
            replayed = response.json()["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as exc:
            error_detail = f"Groq request failed: {exc}"

    original = trace.get("raw_output") or ""
    identical = bool(replayed) and replayed.strip() == original.strip()

    if replayed is not None:
        print("--- REPLAYED OUTPUT (regenerated from trace alone) ---")
        print(replayed)
        print("-" * 60)
        verdict = "IDENTICAL (ignoring surrounding whitespace)" if identical \
            else "DIFFERS (expected: LLM sampling is non-deterministic)"
        print(f"Verdict: {verdict}")
    else:
        print("--- REPLAYED OUTPUT ---")
        print(f"Not available. Reason: {error_detail}")
    print("=" * 60)

    if not args.no_save:
        EVALS_DIR.mkdir(exist_ok=True)
        out_path = EVALS_DIR / "replay_evidence.md"
        lines = [
            "## Replay evidence",
            "",
            f"- **trace_id:** `{trace['trace_id']}`",
            f"- **sampled at:** {trace.get('created_at')}",
            f"- **prompt_version:** `{trace.get('prompt_version')}`",
            f"- **model / params:** `{trace.get('model')}`, temperature `{trace.get('temperature')}`",
            f"- **missing fields:** {', '.join(missing) if missing else 'none'}",
            f"- **replay status:** {'replayed' if replayed is not None else 'failed - ' + str(error_detail)}",
            f"- **verdict:** " + ("identical (whitespace-insensitive)" if identical
                                   else "differs (LLM sampling is non-deterministic)")
            if replayed is not None else "- **verdict:** n/a",
            "",
            "**Original output (from trace):**",
            "",
            "```text",
            original or "(empty)",
            "```",
            "",
            "**Replayed output (from trace alone):**",
            "",
            "```text",
            (replayed or f"(not available: {error_detail})"),
            "```",
            "",
        ]
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Evidence written to {out_path}")


if __name__ == "__main__":
    main()
