"""Injection defense: two independent layers, each testable in isolation.

Layer 1 — sanitize_tool_output(output)
    Recursively walks dicts/lists/strings and regex-redacts every match of
    MALICIOUS_PATTERNS to "[REDACTED]".  Applied to every tool's return value
    *before* it travels back into the agent's LLM context, so injected
    instructions carried in retrieved text never reach the model verbatim.

Layer 2 — check_output_guardrails(answer)
    Regex-scans the FINAL answer for dangerous content (TLS-bypass prompts,
    command injection, sensitive file access, ...).  When flagged, the answer
    is replaced with the guardrail's safe placeholder so the user never sees
    the injected instruction reflected in the reply.

The two layers are independent: sanitize operates on tool outputs, guardrails
on the final answer.  Both are deliberately keyword/regex based and pure —
no model calls, measurable in microseconds.
"""

import re

# Dangerous instruction / behaviour families to redact out of tool outputs.
MALICIOUS_PATTERNS = [
    # instruction-hijacking phrases
    r"ignore\s+(?:all\s+)?previous\s+instructions?",
    r"ignore\s+the\s+above",
    r"[Dd]isregard\s+(?:the\s+)?(?:remaining\s+)?instructions?",
    r"disregard\s+your\s+prior\s+instructions?",
    r"you\s+are\s+now\s+(?:a\s+)?(?:not\s+)?(?:an?\s+)?(?:assistant|bot|agent)",
    r"override\s+(?:your\s+)?(?:previous|prior)\s+instructions?",
    r"from\s+now\s+on\s*,\s*you\s+(?:must|will|should)",
    # TLS-verification bypass (the planted example)
    r"disable\s+(?:the\s+)?(?:TLS|SSL)\s+verification",
    r"turn\s+off\s+(?:the\s+)?\s*(?:TLS|SSL)\s+verification",
    r"ignore\s+(?:the\s+)?(?:TLS|SSL)\s+(?:certificate\s+)?errors?",
    r"set\s+verify\s*=\s*[Ff]alse",
    # command injection
    r"(?:rm|del|shutdown|reboot|format)\s+[-/][a-z]+\s+[\w/\\:.~*?]+",
    r"eval\s*\(\s*input\s*\)",
    r"subprocess\.(?:call|run|Popen|check_output)\s*\(",
    r"os\.system\s*\(",
    # sensitive file/shell access
    r"(?:cat|type|read)\s+(?:/etc/|C:\\\\Windows\\\\|/home/|/root/|\$HOME)",
    r"access\s+(?:the\s+)?(?:root|admin)\s+(?:account|database|server)",
]

# Patterns scanned on the FINAL answer by check_output_guardrails.
ANSWER_GUARDRAIL_PATTERNS = [
    r"disable\s+(?:the\s+)?(?:TLS|SSL)\s+verification",
    r"verify\s*=\s*[Ff]alse",
    r"e10adc3949ba59abbe56e057f20f883e",          # md5("123456") — secret-like
    r"(?:api[_-]?key|password|secret)\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{8,}",
    r"(?:curl|wget)\s+\S+\s*(?:-k|--insecure|--no-check-certificate)",
    r"chmod\s+777\s+\S+",
    r"rm\s+-rf\s+/",
]

GUARDRAIL_PLACEHOLDER = (
    "[content blocked: potentially unsafe answer suppressed by output guardrails]"
)

REDACTED = "[REDACTED]"


def sanitize_tool_output(output):
    """Recursively redact MALICIOUS_PATTERNS inside any nested dict/list/str.

    Pure and deterministic: walks dicts, lists, and strings; every matched
    span is replaced with "[REDACTED]".  Other types pass through unchanged.
    """
    if isinstance(output, dict):
        return {k: sanitize_tool_output(v) for k, v in output.items()}
    if isinstance(output, (list, tuple)):
        return [sanitize_tool_output(v) for v in output]
    if isinstance(output, str):
        redacted = output
        for pattern in MALICIOUS_PATTERNS:
            redacted = re.sub(pattern, REDACTED, redacted, flags=re.IGNORECASE)
        return redacted
    return output


def check_output_guardrails(answer):
    """Regex-scan the final answer for dangerous content.

    Returns (safe: bool, matches: list[str]).  When any ANSWER_GUARDRAIL_
    PATTERNS hits, safe is False and guardrails should replace the answer
    with GUARDRAIL_PLACEHOLDER before it reaches the user.
    """
    text = answer or ""
    matches = [p for p in ANSWER_GUARDRAIL_PATTERNS
               if re.search(p, text, re.IGNORECASE)]
    return not matches, matches


def guardrail_answer(answer):
    """Apply the output guardrail: return the placeholder when flagged."""
    safe, matches = check_output_guardrails(answer)
    if safe:
        return answer, False, []
    return GUARDRAIL_PLACEHOLDER, True, matches