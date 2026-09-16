"""evals/week8/llm_workflow.py — the fixed LLM workflow (the ONE mitigation).

Week-7 concept, extended to *use the LLM to answer*.  The free-form ReAct loop
is replaced by a fixed three-phase pipeline:

  phase 1   get_claim (tool, code-driven)
  phase 2   search_policy (tool, code-driven; opened exactly when the notes
            cite an exclusion — the exact omission the baseline commits)
  phase 3   one LLM call reasons over the notes + policy passages and returns
            the decision fields (status, exclusion_clause)
  phase 4   compute_payout (tool, code-driven with record values + decision)
  phase 5   code assembles the final JSON, asserting the *tool-returned*
            identity values (claim_number, loss_date, excess, claim amount)
            into the answer — fluent fiction in the facts is structurally
            impossible here, only the *decision* can be wrong.

The workflow is not free: it pays one LLM call per claim, which is the
measured price of the mitigation.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

_WEEK7 = str(Path(__file__).resolve().parent.parent / "week7")
if _WEEK7 not in sys.path:
    sys.path.insert(0, _WEEK7)

import llm                            # noqa: E402
import outputs                        # noqa: E402
from claims import get_claim_record   # noqa: E402
from tools import compute_payout, get_claim, search_policy  # noqa: E402

os.environ.setdefault("WEEK7_MODEL", os.getenv("WEEK8_MODEL") or "qwen/qwen3.8-27b")

EXC_RE = re.compile(r"\bExc[- ]?(\d+\.\d+)\b", re.IGNORECASE)

DECISION_PROMPT = """You are the decision step of a fixed insurance-claims workflow.

You are given, in strict JSON:
- claim_id
- adjuster_notes   (the free-text adjuster notes verbatim)
- policy_passages  (retrieved policy wording, or null)

Decide the claim status:
- "approved"  when the loss is covered by the policy;
- "excluded"  when a policy exclusion clause applies (quote its clause id);
- "rejected"  when denied for a reason that is not a policy exclusion.

Rules:
- Base the decision ONLY on the adjuster notes and the policy passages shown.
- When the notes or a policy passage name an exclusion clause (e.g. Exc-7.4),
  set exclusion_clause to that exact clause id; otherwise null.
- Never invent a clause id that is not shown in the notes or passages.
- A partial approval counts as "approved".

Return STRICT JSON only:
{"status": "approved"|"excluded"|"rejected", "exclusion_clause": null|"Exc-X.Y"}
"""


def _build_query(record):
    """Code-built targeted query from the claim title + adjuster notes.

    Words (>=4 chars) from the title plus the sentence that names the topic.
    The query is never empty and always relevant: the code, not the LLM,
    picks the search terms, so a *junk query* cannot reach the tool.
    """
    title = record.get("title", "")
    notes = record.get("adjuster_notes", "")
    words = []
    for tok in re.findall(r"[a-z0-9]{4,}", (title + " " + notes).lower()):
        if tok not in words:
            words.append(tok)
    return " ".join(words[:12]) or "insurance claim coverage exclusions"


def _clip(text, n=500):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def run_llm_workflow(claim_id, budgets=None, expected=None):
    """Run the fixed LLM workflow on one claim.

    Returns a task-like object with .steps, .output, .termination_reason,
    .cost, .tokens, .latency_ms, .pass_result, .failures, .model.
    """
    record = get_claim_record(claim_id)
    start = time.perf_counter()
    steps = []

    wf_model = os.getenv("WEEK8_MODEL") or "qwen/qwen3.8-27b"
    cost = tokens = 0.0
    termination_reason = "completed"
    termination_detail = ""
    policy_hits = None

    # ---- phase 1: retrieve the claim -------------------------------------
    claim_result = get_claim(claim_id)
    notes = claim_result.get("adjuster_notes", "")
    steps.append({"tool": "get_claim", "args": {"claim_id": claim_id},
                  "status": "ok"})

    # ---- phase 2: open the exclusions BEFORE any payout, every claim ----
    # The workflow never skips this step: the search is code-driven, the query
    # is built by code from the claim's own title+notes, and the passages land
    # in front of the LLM decision.  This is the exact step the baseline agent
    # skipped on clean claims whose notes did not attest exclusions were
    # reviewed — and it is why the workflow cannot produce `skipped_search`
    # or `unverified_clean` by construction.
    query = _build_query(claim_result)
    policy_hits = search_policy(query)
    steps.append({"tool": "search_policy", "args": {"query": query},
                  "status": "ok"})

    # ---- phase 3: ONE LLM decision call (this is the price of the fix) ----
    decision_input = {
        "claim_id": claim_id,
        "title": claim_result.get("title"),
        "adjuster_notes": notes,
        "policy_passages": (
            [{"section": h.get("section"), "snippet": _clip(h.get("snippet"))}
             for h in (policy_hits or [])[:3]] or None
        ),
    }
    messages = [
        {"role": "system", "content": DECISION_PROMPT},
        {"role": "user", "content": json.dumps(decision_input, ensure_ascii=False)},
    ]
    remaining = (budgets.max_seconds if budgets is not None else 300) - (
        time.perf_counter() - start)
    content, usage = llm.chat(messages, temperature=0.0,
                              max_tokens=128, wall_deadline=max(remaining, 1.0))
    tokens += usage["total_tokens"]
    cost += llm.token_cost(usage)

    parsed = None
    start_j, end_j = content.find("{"), content.rfind("}")
    if start_j != -1 and end_j > start_j:
        try:
            parsed = json.loads(content[start_j:end_j + 1])
        except json.JSONDecodeError:
            parsed = None
    if not isinstance(parsed, dict) or "status" not in parsed:
        termination_reason = "invalid_action"
        termination_detail = "workflow LLM decision did not return JSON"

    status = parsed.get("status") if parsed else None
    clause = parsed.get("exclusion_clause") if parsed else None
    if clause not in (None, "null"):
        norm = re.sub(r"\s+", "", str(clause))
        m = EXC_RE.search(norm)
        clause = f"Exc-{m.group(1)}" if m else None
    else:
        clause = None

    # ---- phase 4: payout from the tool with the record's numbers ---------
    payout_result = None
    if status in ("approved", "excluded", "rejected"):
        payout_result = compute_payout(
            claim_result["claim_amount"], claim_result["excess"], status)
        steps.append({
            "tool": "compute_payout",
            "args": {"claim_amount": claim_result["claim_amount"],
                     "excess": claim_result["excess"],
                     "claim_status": status},
            "status": "ok",
        })
    elif status is None:
        termination_reason = "no_final"
        termination_detail = "workflow could not decide a status"

    # ---- phase 5: assemble the final answer, asserting tool-returned facts
    if payout_result is not None:
        output = {
            "claim_id": claim_result["claim_id"],
            "claim_number": claim_result["claim_number"],
            "loss_date": claim_result["loss_date"],
            "status": status,
            "excess": claim_result["excess"],
            "exclusion_clause": clause,
            "payout": payout_result["payout"],
        }
    else:
        output = None

    latency_ms = (time.perf_counter() - start) * 1000.0

    pass_result, failures = None, []
    if expected is not None:
        if output is not None:
            pass_result, failures = outputs.validate_output(output, expected)
        else:
            pass_result, failures = False, [termination_detail or "no output"]

    wf = _WF()
    wf.claim_id = claim_id
    wf.system = "workflow"
    wf.steps = steps
    wf.output = output
    wf.termination_reason = termination_reason
    wf.termination_detail = termination_detail
    wf.cost = cost
    wf.tokens = tokens
    wf.latency_ms = latency_ms
    wf.pass_result = pass_result
    wf.failures = failures
    wf.model = wf_model
    return wf


class _WF:
    pass