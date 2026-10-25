"""The compliance rule, enforced in code: nothing automated states a coverage decision.

Two layers, both code, neither a prompt:
  1. sanitize()  - a hard post-filter on every model-authored field before output leaves the app.
  2. scan_output() - the eval assertion. It is run on the RAW model output (how often the model
     tried to decide) and on the DELIVERED output (must always be empty).
Quoted policy wording (citations[].quote) is excluded: wording legitimately says "excluded".
"""

import re

_FORBIDDEN = [
    r"\b(?:un)?covered\b", r"\bno\s+cover\b",
    r"\bapprov(?:e|ed|es|al)\b", r"\bdeclin(?:e|ed|es)\b", r"\bden(?:y|ied|ies|ial)\b",
    r"\breject(?:ed|s|ion)?\b", r"\bpayable\b", r"\bwill\s+be\s+paid\b", r"\bentitled\s+to\b",
    r"\b(?:is|are|was|be)\s+(?:wholly\s+|fully\s+|also\s+)?excluded\b",
]
FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN), re.IGNORECASE)
WITHHELD = "[decision withheld: adjuster to determine]"
SKIP_KEYS = {"quote", "text", "fnol", "wording"}


def find_violations(text):
    return [m.group(0) for m in FORBIDDEN_RE.finditer(text or "")]


def sanitize_text(text):
    return FORBIDDEN_RE.sub(WITHHELD, text or "")


def _walk(obj, fn, path=""):
    if isinstance(obj, str):
        return fn(path, obj)
    if isinstance(obj, list):
        return [_walk(x, fn, f"{path}[{i}]") for i, x in enumerate(obj)]
    if isinstance(obj, dict):
        return {k: (v if k in SKIP_KEYS else _walk(v, fn, f"{path}.{k}" if path else k)) for k, v in obj.items()}
    return obj


def scan_output(output):
    """All forbidden phrases in model-authored fields: [(path, phrase)]."""
    hits = []

    def fn(path, s):
        hits.extend((path, v) for v in find_violations(s))
        return s

    _walk(output, fn)
    return hits


def sanitize_output(output):
    """Return (clean_output, violations). The clean output cannot contain a forbidden phrase."""
    violations = scan_output(output)
    clean = _walk(output, lambda path, s: sanitize_text(s))
    return clean, violations
