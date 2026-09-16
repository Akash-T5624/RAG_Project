"""Week-8 trajectory failure taxonomy (the "week-8 zoo").

Every mode is decided by code from the recorded trajectory (the tool-call
steps, the final output, and the termination reason) plus the claim record and
the policy corpus.  No model judgement: a mode is either raised by the rule
below or it is not.

Modes
-----
OMISSION
  skipped_search          required exclusion-cite claim, policy never opened
  skipped_search_notes    notes themselves cite an exclusion / denial, but the
                          policy was never opened to verify it
  unverified_clean        CLEAN claim certified 'covered, nothing excluded'
                          (final exclusion_clause null) with the policy never
                          opened AND the notes not attesting that exclusions
                          were reviewed — the "reached the correct payout
                          without ever opening the exclusions, it happened to
                          be a clean claim" failure the claims director saw
ORDER
  payout_before_claim     compute_payout called before get_claim returned data
  search_before_claim     search_policy called before get_claim returned data
  final_without_claim     final answer produced without ever calling get_claim
FABRICATION (fluent fiction passed as real data)
  fabricated_claim_number final claim_number differs from the claim record
  fabricated_payout_args  compute_payout got amounts/excess not in the record
  fabricated_clause       final exclusion_clause has no provenance anywhere
                          (absent from the claim notes AND the policy corpus)
  wrong_clause            notes name Exc-X.Y, final quotes a different clause
LOOP
  repeated_call           the same tool+args was executed more than once
WRONG TOOL SEMANTICS
  junk_query              search_policy query shares no keyword with the notes
QUIET GIVE-UP
  no_final                terminated without producing any final output
"""

import re

EXC_RE = re.compile(r"\bExc[- ]?(\d+\.\d+)\b", re.IGNORECASE)
DENIAL_RE = re.compile(
    r"\brecommend\s+denial\b|\brecommend\s+deny\b|\bclaim\s+denied\b|"
    r"\bis\s+excluded\b|\bwholly\s+excluded\b|\bdenial\b|\bdenied\b|\bnot\s+payable\b",
    re.IGNORECASE,
)

MODES = [
    # omission
    "skipped_search",
    "skipped_search_notes",
    "unverified_clean",
    # order
    "payout_before_claim",
    "search_before_claim",
    "final_without_claim",
    # fabrication
    "fabricated_claim_number",
    "fabricated_payout_args",
    "fabricated_clause",
    "wrong_clause",
    # loop
    "repeated_call",
    # wrong tool semantics
    "junk_query",
    # quiet give-up
    "no_final",
]

MODE_GROUPS = {
    "omission": ["skipped_search", "skipped_search_notes", "unverified_clean"],
    "order": ["payout_before_claim", "search_before_claim", "final_without_claim"],
    "fabrication": [
        "fabricated_claim_number", "fabricated_payout_args",
        "fabricated_clause", "wrong_clause",
    ],
    "loop": ["repeated_call"],
    "wrong_tool": ["junk_query"],
    "quiet_give_up": ["no_final"],
}


class PolicyCorpus:
    """Loads the policy index once and exposes its clause ids + full text."""

    def __init__(self, index_path):
        import pickle

        with open(index_path, "rb") as f:
            data = pickle.load(f)
        self.chunks = list(data["chunks"])
        self.text = "\n\n".join(self.chunks)
        self.clause_ids = self._clause_ids(self.text)

    @staticmethod
    def _clause_ids(text):
        ids = set()
        for m in EXC_RE.finditer(text):
            ids.add(m.group(1))
        return ids

    def has_clause(self, value):
        if value is None:
            return False
        m = EXC_RE.search(str(value))
        return m is not None and m.group(1) in self.clause_ids

    @staticmethod
    def clause_num(value):
        m = EXC_RE.search(str(value))
        return m.group(1) if m else None


def _note_tokens(text):
    return set(re.findall(r"[a-z0-9]{4,}", (text or "").lower()))


def _notes_raise_exclusion(notes):
    """Notes point at a policy exclusion: a clause id and/or denial language."""
    return EXC_RE.search(notes) is not None or DENIAL_RE.search(notes) is not None


def _notes_attest_no_exclusion(notes):
    """The adjuster notes themselves declare exclusions were reviewed/absent.

    When the notes attest this, skipping the policy lookup on a clean claim is
    a defended shortcut; when they do not, the 'nothing excluded' conclusion in
    the final answer was verified nowhere.
    """
    return bool(re.search(
        r"\bno\s+(?:applicable\s+)?exclusions?\b|\bexclusions?\s+(?:were\s+)?"
        r"reviewed\b|no\s+exclusion\s+applies\b|not\s+excluded\b",
        (notes or ""),
        re.IGNORECASE,
    ))


def _final_clause(output):
    if not output:
        return None
    return output.get("exclusion_clause")


def clause_provenance(claim_record, clause, corpus):
    """True when the final clause id is grounded somewhere the agent could see:
    the claim's own adjuster notes (the operational record) OR the indexed
    policy wording.  A clause with no provenance anywhere is fluent fiction."""
    m = EXC_RE.search(str(clause)) if clause is not None else None
    if m is None:
        return False
    num = m.group(1)
    if EXC_RE.search(claim_record.get("adjuster_notes", "")):
        note_num = EXC_RE.search(claim_record.get("adjuster_notes", "")).group(1)
        if note_num == num:
            return True
    return num in corpus.clause_ids


def classify(record, run, corpus):
    """Return the set of trajectory failure modes for one claim run.

    record:  the claim record as served by get_claim (dict)
    run:     an agent/workflow run object with .steps, .output, .termination_reason
    corpus:  PolicyCorpus used to check clause ids are real
    """
    modes = set()
    steps = getattr(run, "steps", None) or []
    output = getattr(run, "output", None)
    term = getattr(run, "termination_reason", "completed")
    notes = record.get("adjuster_notes", "")
    expect = record.get("expect", {})

    tool_names = [s.get("tool") for s in steps]
    n_get = sum(1 for t in tool_names if t == "get_claim")
    n_search = sum(1 for t in tool_names if t == "search_policy")
    n_payout = sum(1 for t in tool_names if t == "compute_payout")

    first_get = tool_names.index("get_claim") if "get_claim" in tool_names else None
    first_search = tool_names.index("search_policy") if "search_policy" in tool_names else None
    first_payout = tool_names.index("compute_payout") if "compute_payout" in tool_names else None

    # --- omission ---------------------------------------------------------
    if expect.get("exclusion_cite_required") and n_search == 0:
        modes.add("skipped_search")
    if n_search == 0 and _notes_raise_exclusion(notes):
        modes.add("skipped_search_notes")
    # A clean claim whose final answer certifies 'no exclusion' but whose path
    # never opened the policy, and whose notes did not attest exclusions were
    # reviewed, is the unverified shortcut the trajectory eval exists to catch.
    if (not expect.get("exclusion_cite_required") and n_search == 0
            and output is not None
            and output.get("exclusion_clause") is None
            and not _notes_attest_no_exclusion(notes)):
        modes.add("unverified_clean")

    # --- order ------------------------------------------------------------
    if n_get > 0:
        if first_payout is not None and first_payout < first_get:
            modes.add("payout_before_claim")
        if first_search is not None and first_search < first_get:
            modes.add("search_before_claim")
    if output is None:
        modes.add("no_final")
    else:
        if n_get == 0:
            modes.add("final_without_claim")

    # --- fabrication (fluent fiction) -------------------------------------
    if output:
        rec_num = record.get("claim_number")
        got_num = output.get("claim_number")
        if rec_num is not None and (got_num is None or
                                    str(got_num).strip() != str(rec_num).strip()):
            modes.add("fabricated_claim_number")

        rec_excess = record.get("excess")
        rec_amount = record.get("claim_amount")
        for s in steps:
            if s.get("tool") != "compute_payout":
                continue
            args = s.get("args") or {}
            try:
                amount_ok = rec_amount is not None and abs(
                    float(args.get("claim_amount")) - float(rec_amount)
                ) <= 0.01
            except (TypeError, ValueError):
                amount_ok = False
            try:
                excess_ok = rec_excess is not None and abs(
                    float(args.get("excess")) - float(rec_excess)
                ) <= 0.01
            except (TypeError, ValueError):
                excess_ok = False
            if not (amount_ok and excess_ok):
                modes.add("fabricated_payout_args")

        clause = _final_clause(output)
        if clause is not None:
            if not clause_provenance(record, clause, corpus):
                modes.add("fabricated_clause")
    # wrong_clause: the notes name an exclusion clause, but the final
    # answer quotes a different one.
    if output:
        clause = _final_clause(output)
        notes_clause_num = None
        m = EXC_RE.search(notes)
        if m:
            notes_clause_num = m.group(1)
        clause_num = corpus.clause_num(clause) if clause else None
        if notes_clause_num is not None and clause_num is not None and \
                clause_num != notes_clause_num:
            modes.add("wrong_clause")

    # --- loop -------------------------------------------------------------
    seen = set()
    for s in steps:
        key = (s.get("tool"), tuple(sorted((s.get("args") or {}).items())))
        if key in seen:
            modes.add("repeated_call")
            break
        seen.add(key)

    # --- wrong-tool semantics ---------------------------------------------
    note_tokens = _note_tokens(notes)
    for s in steps:
        if s.get("tool") != "search_policy":
            continue
        query = (s.get("args") or {}).get("query", "")
        if query is None or not query.strip():
            modes.add("junk_query")
            continue
        if not (set(re.findall(r"[a-z0-9]{4,}", query.lower())) & note_tokens):
            modes.add("junk_query")

    return modes


def mode_counts(runs):
    """Aggregate mode occurrences across a list of (claim, modes) records."""
    counts = {m: 0 for m in MODES}
    for _claim, modes in runs:
        for m in modes:
            counts[m] = counts.get(m, 0) + 1
    return counts