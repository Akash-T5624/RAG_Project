import json
import os
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent / "backend"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Real failed-claim traces picked from the production pool (backend/traces/).
# 4cb9d33f: "tyres and tubes damage cover" -> refused, no coverage explained.
# a2d35a99: "policy number?"            -> refused (sample policy has no number).
REGRESSION_TRACES = [
    {"trace_id": "4cb9d33f-1b00-4a38-8b55-499894fd261f", "expect": "refusal"},
    {"trace_id": "a2d35a99-fadc-4be9-978d-8d84606c1a41", "expect": "refusal"},
]

REPLAY_FIELDS = ["question", "llm_messages", "model", "temperature", "raw_output"]


def _load_env():
    env_path = BACKEND / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _replay(trace: dict) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "paste_your_groq_api_key_here":
        raise RuntimeError("GROQ_API_KEY not configured")
    resp = requests.post(
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
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _expected_class(output: str) -> str:
    """Classify an answer as 'refusal' (not found) or 'answered'."""
    o = (output or "").strip().lower()
    return "refusal" if o.startswith("i could not find") or "not find the answer" in o else "answered"


def run_regression(out_path: str | Path, force: bool = False, delay: float = 1.0) -> list:
    """Replay each regression trace and report pass/fail per trace.

    Results are cached to out_path. Returns list of result dicts.
    """
    out_path = Path(out_path)
    results = []
    if out_path.exists() and not force:
        results = json.loads(out_path.read_text(encoding="utf-8"))
        return results

    # Prefer the committed redacted snapshot (replay fields verbatim from the
    # real trace). Fall back to the live trace file if available.
    snapshot_path = HERE / "regression_snapshot.json"
    traces_by_id = {}
    if snapshot_path.exists():
        for snap in json.loads(snapshot_path.read_text(encoding="utf-8")):
            traces_by_id[snap["trace_id"]] = snap
    live = {spec["trace_id"]: "" for spec in REGRESSION_TRACES}
    for spec in REGRESSION_TRACES:
        trace_path = BACKEND / "traces" / f'{spec["trace_id"]}.json'
        if trace_path.exists():
            live[spec["trace_id"]] = json.loads(trace_path.read_text(encoding="utf-8"))

    for spec in REGRESSION_TRACES:
        trace = traces_by_id.get(spec["trace_id"]) or live.get(spec["trace_id"])
        if not trace:
            raise SystemExit(
                f"Regression trace not found for {spec['trace_id']} "
                f"(no committed snapshot, and {BACKEND / 'traces'} is empty)."
            )

        original = trace.get("raw_output") or ""
        try:
            replayed = _replay(trace)
        except Exception as exc:
            replayed = None
            error = str(exc)
        else:
            error = None

        if replayed is None:
            passed = False
            detail = f"replay failed: {error}"
        else:
            orig_class = _expected_class(original)
            replayed_class = _expected_class(replayed)
            # Regression check: the regenerated answer must stay in the same class
            # as the original failure (no drift into fabrication).
            passed = (replayed_class == orig_class)
            detail = f"original={orig_class!r} replayed={replayed_class!r}"

        results.append({
            "trace_id": spec["trace_id"],
            "question": trace.get("question"),
            "expect": spec["expect"],
            "original_output": original,
            "replayed_output": replayed,
            "passed": passed,
            "detail": detail,
        })

    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results
