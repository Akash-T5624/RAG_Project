import json
import os
import re
import time
from pathlib import Path

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Retry budget for the rate-limited free-tier Groq endpoint. The judge hits the
# API 27 times per run; without backoff we see 429 bursts, which previously
# left ~1/3 of the cases as ERROR and silently lowered the recorded agreement.
MAX_ATTEMPTS = 6
BASE_BACKOFF_S = 5.0
BACKOFF_MULT = 1.8


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

_JUDGE_DIR = Path(__file__).resolve().parent


def load_judge_prompt(version: str) -> str:
    path = _JUDGE_DIR / f"judge_{version}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Judge prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def _parse_verdict(text: str) -> str:
    """Extract the ONE-word verdict from the judge output."""
    m = re.search(r"\b(ACCEPT|REJECT)\b", text.upper())
    return m.group(1) if m else "PARSE_FAIL"


def _post_with_retry(payload: dict) -> requests.Response:
    """POST with exponential backoff on 429 (rate limit) / 5xx / network errors."""
    attempt = 0
    backoff = BASE_BACKOFF_S
    last_err = None
    while attempt < MAX_ATTEMPTS:
        try:
            resp = requests.post(GROQ_API_URL, timeout=90, **payload_headers_json(payload))
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                last_err = f"HTTP {resp.status_code}"
            else:
                last_err = f"HTTP {resp.status_code}"
                if resp.status_code < 500:
                    # 4xx other than 429 is not transient — fail fast.
                    raise requests.HTTPError(f"{resp.status_code}: {resp.text[:200]}")
        except requests.RequestException as exc:
            last_err = str(exc)
        attempt += 1
        wait = backoff * (BACKOFF_MULT ** (attempt - 1))
        print(f"    ... judge call failed ({last_err}); retrying in {wait:.0f}s "
              f"({attempt}/{MAX_ATTEMPTS})")
        time.sleep(wait)
    raise RuntimeError(f"judge call failed after {MAX_ATTEMPTS} attempts: {last_err}")


def payload_headers_json(payload: dict) -> dict:
    return {
        "headers": {
            "Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}",
            "Content-Type": "application/json",
        },
        "json": payload,
    }


def _chat(prompt: str, temperature: float) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "paste_your_groq_api_key_here":
        raise RuntimeError("GROQ_API_KEY not configured")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    resp = _post_with_retry({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
    })
    return resp.json()["choices"][0]["message"]["content"]


def run_judge(prompt_template: str, adjuster_notes: str, summary: str) -> tuple[str, str]:
    """Run the judge for one case. Returns (verdict, raw_output)."""
    prompt = prompt_template.replace("{adjuster_notes}", adjuster_notes) \
                            .replace("{summary}", summary)
    raw = _chat(prompt, temperature=0.0)
    return _parse_verdict(raw), raw


def run_judge_with_reason(prompt_template: str, adjuster_notes: str, summary: str) -> tuple[str, str]:
    """Judge + one-line reason. Used ONLY for post-hoc disagreement analysis.

    The extra reason is appended after the binary verdict so the eval label stays
    binary and comparable; the reason is purely diagnostic.
    """
    prompt = prompt_template.replace("{adjuster_notes}", adjuster_notes) \
                            .replace("{summary}", summary) \
                            .replace("No explanation. No punctuation. One word only.",
                                     "Then, on the line after the verdict, give one "
                                     "short sentence explaining the verdict.")
    raw = _chat(prompt, temperature=0.0)
    return _parse_verdict(raw), raw


def run_judge_all(
    cases: dict,
    summaries: dict,
    version: str,
    out_path: str | Path,
    force: bool = False,
    delay: float = 0.3,
) -> dict:
    """Run (cache) the judge over every case. Returns {case_id: {verdict, raw}}."""
    out_path = Path(out_path)
    prompt_template = load_judge_prompt(version)

    results = {}
    if out_path.exists() and not force:
        results = json.loads(out_path.read_text(encoding="utf-8"))

    # Re-run any case whose cached verdict is ERROR or PARSE_FAIL (e.g. earlier
    # rate-limited runs left real cases as ERROR).
    if not force:
        stale = [cid for cid, r in results.items()
                 if r.get("verdict") in ("ERROR", "PARSE_FAIL")]
        for cid in stale:
            results.pop(cid)

    missing = [cid for cid in cases if cid not in results or force]
    if not missing:
        return results

    total = len(missing)
    for i, case_id in enumerate(missing, 1):
        case = cases[case_id]
        try:
            verdict, raw = run_judge(
                prompt_template,
                case["adjuster_notes"],
                summaries[case_id],
            )
        except Exception as exc:
            verdict, raw = "ERROR", f"[judge error: {exc}]"
        results[case_id] = {"verdict": verdict, "raw_output": raw}
        print(f"  [{i}/{total}] {case_id}: {verdict}")
        if delay and i < total:
            time.sleep(delay)

    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return results
