"""PII line for the capstone: names, addresses and injuries out of logs AND out of MCP results.

Builds on the Week 5/11 redactor (backend/trace_log.redact_text: claim refs, labelled and titled
names, long ids) and adds what it missed: street addresses and injury descriptions.
install() patches trace_log.redact_text so build_trace() (which redacts every free-text field
before any disk write) uses the extended version. Redaction is pattern-based: a bare name with no
title or label ("Rahul Sharma") is NOT caught. That limit is recorded in docs/WEAKNESSES.md.
"""

import re

import capstone.core.paths  # noqa: F401  (puts backend/ on sys.path)
import trace_log

REDACTED = "[REDACTED]"
_STREET = (r"(?:Road|Rd|Street|St|Lane|Cross|Apartments|Park|Nagar|Colony|Layout|Avenue|Enclave|Marg|Nilayam)")
_ADDRESS = re.compile(
    rf"\b\d{{1,4}}[A-Za-z]?\s+(?:[A-Z][A-Za-z0-9.'\-]*\s+){{0,4}}?{_STREET}(?:\s+{_STREET})?"
    r"(?:,\s*[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)*(?:\s+\d{6})?")
_INJURY_LABEL = re.compile(r"(?i)\b(injur(?:y|ies)(?:\s+noted|\s+reported)?\s*[:\-]\s*)[^.]+")
_INJURY_TERMS = re.compile(
    r"(?i)\b(?:whiplash|fractur\w*|lacerat\w*|concuss\w*|sprain\w*|dislocat\w*|lumbar\s+strain|bruis\w*|back\s+injury)\b")
_REPORTED_BY = re.compile(r"(?i)\b(reported by|claimant|policyholder name)\s*[:\-]?\s+(?=[A-Z])")

_ORIG = getattr(trace_log, "_capstone_orig_redact", trace_log.redact_text)


def redact(text):
    if not isinstance(text, str) or not text:
        return text
    text = _ORIG(text)
    text = _ADDRESS.sub(REDACTED, text)
    text = _INJURY_LABEL.sub(lambda m: m.group(1) + REDACTED, text)
    text = _INJURY_TERMS.sub(REDACTED, text)
    return text


def redact_obj(obj):
    """Redact every string inside a JSON-like structure (used on MCP results and span details)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


def install():
    """Route trace_log's write-time redaction through the extended redactor (idempotent)."""
    trace_log._capstone_orig_redact = _ORIG
    trace_log.redact_text = redact


def leaks(text, pii):
    """Which deny-list items (names, addresses, injuries) appear in `text`? Used by tests and the PII scan."""
    low = (text or "").lower()
    found = []
    for kind in ("names", "addresses", "injuries"):
        for item in pii.get(kind, []):
            if item.lower() in low:
                found.append(f"{kind}:{item}")
    return found


install()
