import json
import os
import re
import time
from pathlib import Path

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

_SUMMARY_PROMPT = (
    "You are an insurance claims processor. Given raw adjuster notes for a "
    "motor-vehicle insurance claim, produce a concise claim summary.\n\n"
    "The summary must contain:\n"
    "- The claim number exactly as given (e.g. CLM-2026-00412).\n"
    "- The date of loss, if stated.\n"
    "- The excess/deductible amount, if stated.\n"
    "- What is covered and what is NOT covered under this claim.\n"
    "- If a claim is denied, state the denial and cite the exclusion clause id "
    "explicitly (e.g. 'Exclusion Exc-7.4').\n"
    "- A one-line recommendation (approve / approve partial / deny).\n\n"
    "Do not invent coverage beyond what the notes describe.\n\n"
    "Adjuster notes:\n{notes}\n\n"
    "Claim summary:"
)


def _load_env():
    env_path = Path(__file__).resolve().parent.parent.parent / "backend" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def generate_summary(notes: str, model: str | None = None) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "paste_your_groq_api_key_here":
        raise RuntimeError("GROQ_API_KEY not configured")

    model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    prompt = _SUMMARY_PROMPT.format(notes=notes)

    resp = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def generate_all(
    cases_path: str | Path,
    out_path: str | Path | None = None,
    delay: float = 0.5,
    force: bool = False,
) -> dict:
    """Generate summaries for all cases; cache results on disk.

    Returns dict mapping case_id -> summary string.
    """
    cases_path = Path(cases_path)
    out_path = Path(out_path) if out_path else cases_path.parent / "generated_summaries.json"

    cases = {}
    for line in cases_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cases[obj["id"]] = obj

    if not force and out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        missing = [cid for cid in cases if cid not in existing]
        if not missing:
            return existing
        # Regenerate only missing ones

    summaries = {}
    if out_path.exists() and not force:
        summaries = json.loads(out_path.read_text(encoding="utf-8"))

    total = len(cases)
    for i, (case_id, case) in enumerate(cases.items(), 1):
        if case_id in summaries and not force:
            continue
        try:
            summary = generate_summary(case["adjuster_notes"])
        except Exception as exc:
            summary = f"[GENERATION FAILED: {exc}]"
        summaries[case_id] = summary
        print(f"  [{i}/{total}] {case_id}: {'OK' if not summary.startswith('[GENERATION') else 'FAIL'}")
        if delay and i < total:
            time.sleep(delay)

    out_path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Saved {len(summaries)} summaries to {out_path}")
    return summaries
