import json
import re
import uuid
import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
TRACE_DIR = PROJECT_DIR / "traces"            # random-sample pool
DEMO_TRACE_DIR = PROJECT_DIR / "traces_demo"  # bonus: curated demo claims
TRACE_JSONL = PROJECT_DIR / "trace.jsonl"      # consolidated append-only log

REDACTED = "[REDACTED]"

# ---------------------------------------------------------------------------
# Redaction patterns (insurance-claims domain)
# ---------------------------------------------------------------------------

# "Claim No: 123456789", "Policy Number ABC-123456", "claim #987654"
_LABELED_NUMBER = re.compile(
    r"\b((?:claim|policy|certificate|proposal)\s*(?:no\.?|number|#)\s*[:#\-]?\s*)"
    r"[A-Za-z]{0,4}[-/]?\d[\dA-Za-z\-/]{4,}",
    re.IGNORECASE,
)

# "Claimant: John A Kumar", "Nominee - Mary Smith", "INSURED NAME: R. Patel"
_LABELED_PERSON = re.compile(
    r"\b((?:claimant|policyholder|insured|proposer|nominee|beneficiary"
    r"|life\s+assured|deceased|spouse)\s*(?:name)?\s*[:\-]\s*)"
    r"[A-Z](?:[a-z]+|\.)?(?:\s+[A-Z](?:[a-z]+|\.)?){0,3}",
    re.IGNORECASE,
)

# "Mr Rahul Sharma", "Mrs. Priya Patel", "Dr A. B. Rao"
_TITLED_NAME = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z](?:[a-z]+|\.)?){0,2}\b"
)

# Bare long identifiers: "LIC-123456789", "9876543210"
_STANDALONE_ID = re.compile(r"\b[A-Z]{2,5}[-/]\d{6,14}\b|\b\d{10,16}\b")


def redact_text(text):
    """Mask claimant identifiers. Runs before any disk write, never after."""
    if not isinstance(text, str) or not text:
        return text
    text = _LABELED_NUMBER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _LABELED_PERSON.sub(lambda m: m.group(1) + REDACTED, text)
    text = _TITLED_NAME.sub(REDACTED, text)
    text = _STANDALONE_ID.sub(REDACTED, text)
    return text


def _redact_messages(messages):
    out = []
    for msg in messages or []:
        out.append({
            "role": msg.get("role"),
            "content": redact_text(msg.get("content", "")),
        })
    return out


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Trace construction / persistence
# ---------------------------------------------------------------------------

def build_trace(*, endpoint, session_id, doc_id, prompt_version, question,
                llm_messages, retrieval_results, model, temperature, stream,
                raw_output, latency_ms, error=None):
    """Assemble one complete trace. ALL free text is redacted here."""
    return {
        "trace_id": str(uuid.uuid4()),
        "created_at": _now_iso(),
        "endpoint": endpoint,
        "session_id": session_id,
        "doc_id": doc_id,
        "prompt_version": prompt_version,
        "model": model,
        "temperature": temperature,
        "stream": stream,
        "latency_ms": round(float(latency_ms), 1),
        "status": "error" if error else "ok",
        "error": error,
        "question": redact_text(question),
        # Exact payload sent to the LLM -- replay needs nothing else.
        "llm_messages": _redact_messages(llm_messages),
        "retrieval": {
            "top_k": len(retrieval_results or []),
            "results": [
                {
                    "rank": r["rank"],
                    "chunk_index": r.get("chunk_index"),
                    "page_number": r["metadata"]["page_number"],
                    "score": round(float(r["score"]), 4),
                    "chunk_preview": redact_text(r["chunk"][:240]),
                }
                for r in (retrieval_results or [])
            ],
        },
        "raw_output": redact_text(raw_output or ""),
    }


def save_trace(trace, directory=None):
    """Persist a trace atomically. Input must come from build_trace()."""
    target = Path(directory) if directory else TRACE_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{trace['trace_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def list_trace_ids(directory=None):
    target = Path(directory) if directory else TRACE_DIR
    target.mkdir(parents=True, exist_ok=True)
    return sorted(p.stem for p in target.glob("*.json"))


def load_trace(trace_id, directory=None):
    target = Path(directory) if directory else TRACE_DIR
    path = target / f"{trace_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"No trace named {trace_id} in {target}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_trace_jsonl(trace):
    """Append one trace as a single JSON line to trace.jsonl.

    Input must come from build_trace().  The file is append-only and one
    JSON object per line (JSONL / newline-delimited JSON format).
    """
    TRACE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACE_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")
