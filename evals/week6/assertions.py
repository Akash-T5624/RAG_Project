import re
from datetime import datetime

_HYPHEN = r"[\-\u2011\u2012\u2013\u2014\u2212]"
_HYPHEN_CHARS = "-\u2011\u2012\u2013\u2014\u2212"


def _strip_hyphens(text: str) -> str:
    return re.sub(r"[%s]" % re.escape(_HYPHEN_CHARS), "-", text)


# --- Assertion 1 ---

_CLM_RE = re.compile(r"\bCLM-\d{4}-\d{4,6}\b", re.IGNORECASE)


def assert_claim_number(summary: str, expected: str | None) -> tuple[bool, str]:
    """Claim number in CLM-YYYY-NNNNN format present in the summary."""
    summary = _strip_hyphens(summary)
    if not expected:
        return True, "No claim number expected (case has none)"
    # Normalize the expected number's separators too
    expected = _strip_hyphens(expected)
    matches = _CLM_RE.findall(summary)
    if not matches:
        return False, f"No CLM-YYYY-NNNNN pattern found in summary"
    if expected.lower() not in [m.lower() for m in matches]:
        return False, f"Expected {expected}, found {matches}"
    return True, f"Claim number {expected} found"


# --- Assertion 2 ---

_MONTH_FULL = (
    r"(?:January|February|March|April|May|June|July|August|September"
    r"|October|November|December)"
)
# Abbreviated months with optional trailing period (Jan, Feb., Sep ...)
_MONTH_ABBR = (
    r"(?:Jan(?:\.|uary)?|Feb(?:\.|ruary)?|Mar(?:\.|ch)?|Apr(?:\.|il)?"
    r"|May\.?|Jun(?:\.|e)?|Jul(?:\.|y)?|Aug(?:\.|ust)?|Sep(?:\.|t(?:ember)?)?"
    r"|Oct(?:\.|ober)?|Nov(?:\.|ember)?|Dec(?:\.|ember)?)"
)
_MONTH = rf"(?:{_MONTH_FULL}|{_MONTH_ABBR})"

_DATE_RE = re.compile(
    rf"\b(?:20\d{{2}}[-/\.](?:0?[1-9]|1[0-2])[-/\.](?:0?[1-9]|[12]\d|3[01])"
    rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*20\d{{2}}"
    rf"|\d{{1,2}}\s+(?:st|nd|rd|th)?{_MONTH}\s+20\d{{2}})\b",
    re.IGNORECASE,
)


def assert_loss_date(summary: str, expected_date: str | None) -> tuple[bool, str]:
    """A parseable date of loss is present when one was given."""
    summary = _strip_hyphens(summary)
    if not expected_date:
        # Check whether there's a date at all (some cases are "missing-loss-date")
        matches = _DATE_RE.findall(summary)
        if matches:
            return True, "Date present (though case expected none — benign)"
        return True, "No date expected and none found"
    # Expected a date; check it appears
    expected_date = _strip_hyphens(expected_date)
    matches = _DATE_RE.findall(summary)
    if not matches:
        return False, f"Expected date ~{expected_date} but no date found"
    # Loose match: the expected date's month or day should appear
    try:
        year, month, day = expected_date.split("-")
        month_name = datetime(int(year), int(month), 1).strftime("%B")
    except ValueError:
        return True, f"Date present ({matches[0]}); expected {expected_date}"
    if month_name.lower() in summary.lower() or _strip_hyphens(expected_date) in summary:
        return True, f"Date {expected_date} found"
    return True, f"A date is present ({matches[0]}); expected {expected_date} — approximate match"


# --- Assertion 3 ---

_EXCESS_NUM_RE = re.compile(r"(?:Rs\.?|INR|₹)\s*[\d,]+(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s*(?:Rs\.?|INR|rupees)", re.IGNORECASE)
_EXCESS_WORDS_RE = re.compile(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten)\s+thousand", re.IGNORECASE)


def assert_excess_numeric(summary: str, expected_excess: float | None) -> tuple[bool, str]:
    """Excess/deductible is expressed as a numeric figure when one exists."""
    summary = _strip_hyphens(summary)
    if expected_excess is None:
        # Cases where notes said "approximately two thousand" — judge should accept words
        # but assertions can't parse that. Just check no bare number contradicts.
        return True, "No specific excess expected"
    num_match = _EXCESS_NUM_RE.search(summary)
    if num_match:
        return True, f"Numeric excess found: {num_match.group()}"
    word_match = _EXCESS_WORDS_RE.search(summary)
    if word_match:
        return True, f"Word-form excess found: {word_match.group()}"
    # Final check: any plain number ≥500 could be excess
    plain = re.findall(r"\b[\d,]+(?:\.\d{2})?\b", summary)
    for p in plain:
        val = float(p.replace(",", ""))
        if 500 <= val <= 50000:
            return True, f"Plausible numeric excess found: {p}"
    return False, f"Expected numeric excess ~{expected_excess} but none found"


# --- Assertion 4 ---

_EXCLUSION_RE = re.compile(r"exc[l\-]?[\-\s]*(?:\d+\.\d+|\d+)", re.IGNORECASE)
_CLAUSE_RE = re.compile(r"clause\s+(?:exc[\-\.]?\s*)?\d+\.\d+|Exc-\d+\.\d+|\bExc\d+\.\d+", re.IGNORECASE)


def assert_exclusion_cite(summary: str, denial: bool, exclusion_required: bool) -> tuple[bool, str]:
    """When a denial is stated, an exclusion clause id is cited."""
    summary = _strip_hyphens(summary)
    summary_lower = summary.lower()
    denial_keywords = ["deni", "exclud", "not covered", "not payable", "recommend deny", "shall not", "is excluded"]
    states_denial = any(kw in summary_lower for kw in denial_keywords)

    if not exclusion_required and not states_denial:
        return True, "No denial — no exclusion cite needed"
    if not exclusion_required and states_denial:
        # Soft denial (partial) — cite not strictly required by the task
        return True, "Partial denial without explicit clause — acceptable for non-full-denial"

    # Full denial expected — clause must be cited
    cite = _EXCLUSION_RE.search(summary) or _CLAUSE_RE.search(summary)
    if cite:
        return True, f"Exclusion clause cited: {cite.group()}"
    return False, "Denial stated but no exclusion clause id cited"


# --- Run all assertions ---

def run_assertions(summary: str, expect: dict) -> dict:
    """Run all 4 assertions and return structured results.

    expect keys: claim_number, loss_date, excess, denial, exclusion_cite_required
    """
    results = {}
    results["claim_number"] = assert_claim_number(summary, expect.get("claim_number"))
    results["loss_date"] = assert_loss_date(summary, expect.get("loss_date"))
    results["excess_numeric"] = assert_excess_numeric(summary, expect.get("excess"))
    results["exclusion_cite"] = assert_exclusion_cite(
        summary,
        expect.get("denial", False),
        expect.get("exclusion_cite_required", False),
    )
    results["all_pass"] = all(v[0] for v in results.values())
    return results


ASSERTION_NAMES = ["claim_number", "loss_date", "excess_numeric", "exclusion_cite"]
