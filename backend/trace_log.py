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

# Claim references: "CLM-2026-00418" (claims follow the claimant, so they are
# redacted with the rest of the PII -- on write, never later).
_CLAIM_REF = re.compile(r"\bCLM-\d{4}-\d{3,6}\b", re.IGNORECASE)


def redact_text(text):
    """Mask claimant identifiers. Runs before any disk write, never after."""
    if not isinstance(text, str) or not text:
        return text
    text = _CLAIM_REF.sub(REDACTED, text)
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


# Timestamps this process writes can be shifted as a block.  Only the drill
# harness uses it: it replays recorded traffic so the log looks like "yesterday"
# without waiting a day.  Production never calls set_time_offset().
_TIME_OFFSET_SEC = [0.0]


def set_time_offset(seconds: float) -> None:
    _TIME_OFFSET_SEC[0] = float(seconds)


def now_utc() -> datetime.datetime:
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=_TIME_OFFSET_SEC[0]))


def _now_iso():
    return now_utc().isoformat()


# ---------------------------------------------------------------------------
# Input slice: what kind of question was this?  (log field, not a guess)
# ---------------------------------------------------------------------------

# Ordered: money-shaped questions are "payout" even when they also imply a
# coverage decision, because that is the slice the support drill starts from.
_INPUT_RULES = [
    ("payout", r"\b(payout|payable|amount (?:to|we|will) pay|refund|settle"
               r"|deductibl|net (?:amount|payable)|out of pocket"
               r"|how much (?:do|will|would|should) (?:we|you|be)"
               r"|how much (?:will|would|can) (?:be )?(?:paid|paid out|payable)"
               r"|will be (?:paid|payable)|claims? .{0,50}\b(?:pay|paid|amount|estimate))"),
    ("policy-term", r"\b(deductibl|premium|policy period|free look|cancel|renew"
                    r"|bonus|ncb|waiting|grace|sum assured|idv|policy number"
                    r"|insured declared value|add-?ons?|roadside|registration)"),
    ("claim-procedure", r"\b(notif|intimat|document|surveyor|repair|claim form"
                        r"|steps? to|how do i claim|turnaround|helpline"
                        r"|police report|inspect)"),
    ("coverage", r"\b(cover(?:ed|age)?|exclud|eligib|insured|lapse"
                 r"|under the policy|qualif|benefit)"),
]

# Words that mark an answer as a coverage decision rather than a lookup.
_COVERED_RE = re.compile(
    r"\b(is|are|will be|remains?)\s+\*{0,2}cover(?:ed|age)\b"
    r"|\bcovered under\b|\bpayable amount (?:is|of)\b|\bwill be paid\b"
    r"|\bis \*{0,2}payable\b|\bapproved\b|\benjoy(?:s|ed)? cover(?:age)?\b",
    re.IGNORECASE,
)
_NOT_COVERED_RE = re.compile(
    r"\bnot cover(?:ed|age)\b|\bnot payable\b|\bnot eligible\b|\bexcluded\b"
    r"|\bis an exclusion\b|\bwill be rejected\b|\bclaim is rejected\b"
    r"|\brejected\b|\bdenied\b|\bno amount is payable\b"
    r"|pay(?:able|out)?\D{0,12}\*{0,2}(?:₹|rs\.?|inr)?\*{0,2}\s*0\b",
    re.IGNORECASE,
)
_REFUSAL_RE = re.compile(r"^i could not find|not find the answer in the provided",
                         re.IGNORECASE)
_PAYOUT_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?\s?|inr\s?)\s?[\d][\d,]*(?:\.\d{1,2})?",
    re.IGNORECASE,
)
_EXCLUSION_PHRASE_RE = re.compile(
    r"important exclusions|section 5|exclusion[s]?\b|wear and tear|"
    r"gradual deterioration|without a valid (?:driving )?licence|"
    r"under the influence|mechanical or electrical breakdown|"
    r"racing, speed testing|consequential loss|policy period|"
    r"geographical area",
    re.IGNORECASE,
)


def classify_input(question: str) -> str:
    """Cheap, deterministic input slice recorded on every trace."""
    q = (question or "").lower()
    for name, pattern in _INPUT_RULES:
        if re.search(pattern, q, re.IGNORECASE):
            return name
    return "general"


def index_output(text: str) -> dict:
    """Index the ANSWER (not just the input) so output-shaped complaints
    ("it said it was covered") become a query instead of a full-text crawl.

    Deliberately limited to what the answer itself says.  Whether the retrieval
    context backed that answer up is a separate question -- see the drill.
    """
    text = text or ""
    refusal = bool(_REFUSAL_RE.search(text.strip()))
    if refusal:
        verdict = "refusal"
    elif _NOT_COVERED_RE.search(text):
        verdict = "not_covered"
    elif _COVERED_RE.search(text):
        verdict = "covered"
    elif text.strip():
        verdict = "unknown"
    else:
        verdict = "empty"

    amounts = []
    for m in _PAYOUT_AMOUNT_RE.finditer(text):
        raw = m.group().replace("₹", "").replace(",", "")
        raw = re.sub(r"(?i)^rs\.?\s?", "", raw).strip()
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 0 and value not in amounts:
            amounts.append(value)

    return {
        "coverage_verdict": verdict,
        "exclusion_phrases": sorted({m.group().lower()
                                     for m in _EXCLUSION_PHRASE_RE.finditer(text)})[:8],
        "amounts_quoted": amounts[:5],
        "chars": len(text),
    }



# ---------------------------------------------------------------------------
# Trace construction / persistence
# ---------------------------------------------------------------------------

def _spans_block(spans, total_ms):
    """Normalise the per-step spans the caller measured.

    Every span carries its own latency; the generation span also carries the
    tokens and USD cost of its step, so a trace can be read per-step instead
    of as one lump.
    """
    out = []
    for s in spans or []:
        out.append({
            "name": s.get("name"),
            "start_ms": round(float(s.get("start_ms", 0.0)), 1),
            "duration_ms": round(float(s.get("duration_ms", 0.0)), 1),
            "status": s.get("status", "ok"),
            "tokens": s.get("tokens"),
            "cost_usd": round(float(s.get("cost_usd", 0.0)), 8),
            "detail": s.get("detail"),
        })
    if not any(s["name"] == "total" for s in out):
        out.append({
            "name": "total",
            "start_ms": 0.0,
            "duration_ms": round(float(total_ms), 1),
            "status": "ok",
            "tokens": None,
            "cost_usd": None,
            "detail": None,
        })
    return out


def _usage_block(usage) -> dict:
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens", 0) or (prompt + completion)),
    }


def _retrieval_block(retrieval_results) -> dict:
    """Retrieval results + the retrieved context ids, in one block.

    context_ids is the one-hop diagnosis: with it you can tell "retrieval
    never surfaced the exclusions schedule" from "retrieval had it and the
    model ignored it" without re-running anything.
    """
    results = retrieval_results or []
    context_ids = [
        {
            "rank": r.get("rank"),
            "chunk_id": r.get("chunk_id"),
            "chunk_index": r.get("chunk_index", r.get("chunk_idx")),
            "page_number": (r.get("metadata") or {}).get("page_number"),
            "score": round(float(r.get("score", 0.0)), 4),
            "injected": bool(r.get("injected", False)),
        }
        for r in results
    ]
    return {
        "top_k": len(results),
        "context_ids": context_ids,
        "results": [
            {
                "rank": r["rank"],
                "chunk_id": r.get("chunk_id"),
                "chunk_index": r.get("chunk_index", r.get("chunk_idx")),
                "page_number": r["metadata"]["page_number"],
                "score": round(float(r["score"]), 4),
                "injected": bool(r.get("injected", False)),
                "chunk_preview": redact_text(r["chunk"][:240]),
            }
            for r in results
        ],
    }


def build_trace(*, endpoint, session_id, doc_id, prompt_version, question,
                llm_messages, retrieval_results, model, temperature, stream,
                raw_output, latency_ms, error=None, spans=None, usage=None,
                cost=None, created_at=None):
    """Assemble one complete trace. ALL free text is redacted here."""
    usage_block = _usage_block(usage)
    cost_block = dict(cost or {})
    for stage in ("retrieval", "generation", "tools"):
        cost_block.setdefault(stage, 0.0)
    cost_block["total"] = round(
        cost_block.get("total", cost_block["retrieval"]
                       + cost_block["generation"] + cost_block["tools"]), 8)
    question_redacted = redact_text(question)
    output_redacted = redact_text(raw_output or "")
    return {
        "trace_id": str(uuid.uuid4()),
        "created_at": created_at or _now_iso(),
        "endpoint": endpoint,
        "session_id": session_id,
        "doc_id": doc_id,
        "prompt_version": prompt_version,
        "model": model,
        "temperature": temperature,
        "stream": stream,
        "input_type": classify_input(question_redacted),
        "latency_ms": round(float(latency_ms), 1),
        "spans": _spans_block(spans, latency_ms),
        "usage": usage_block,
        "cost_usd": cost_block,
        "status": "error" if error else "ok",
        "error": error,
        "question": question_redacted,
        # Exact payload sent to the LLM -- replay needs nothing else.
        "llm_messages": _redact_messages(llm_messages),
        "retrieval": _retrieval_block(retrieval_results),
        "output_index": index_output(output_redacted),
        "raw_output": output_redacted,
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


def load_trace_path(trace_id, directory=None) -> Path:
    target = Path(directory) if directory else TRACE_DIR
    return target / f"{trace_id}.json"


def save_trace_jsonl(trace):
    """Append one trace as a single JSON line to trace.jsonl.

    Input must come from build_trace().  The file is append-only and one
    JSON object per line (JSONL / newline-delimited JSON format).
    """
    TRACE_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACE_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")
