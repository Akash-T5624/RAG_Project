"""The two narrow specialist workers of the claims squad.

Design constraint (the only plausible source of a multi-agent win): each
specialist gets a narrow prompt and a *subset* of the single agent's tools.

  coverage/exclusions worker   search_policy, retrieve_policy_exclusions
                               (NO get_claim, NO compute_payout, NO escalate)
  adjuster-note summary worker get_claim, validate_claim_number
                               (NO search_policy, NO compute_payout)

Each worker receives ONE narrow slice of the claim - its own system prompt and
a single user message - never the manager's conversation, never the other
worker's output, never the raw policy dump.  That is the context strategy under
test: the multiplier is measured on this strategy, not on "re-send everything
to everyone".

A worker that asks for a tool outside its allow-list is stopped in code
(``tool_not_allowed``) - the narrow-tool rule is enforced, not just prompted.
"""

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_WEEK7 = Path(__file__).resolve().parent.parent / "week7"
if str(_WEEK7) not in sys.path:
    sys.path.insert(0, str(_WEEK7))

import llm                                    # noqa: E402  (week-7 stack)
from agent import parse_tool_json             # noqa: E402
from tools import call_tool                   # noqa: E402

from handoffs import make_call                # noqa: E402
from race_config import (                     # noqa: E402
    CALL_PACE_S,
    COVERAGE_WORKER_TOOLS,
    SUMMARY_WORKER_TOOLS,
    WORKER_MAX_LLM_CALLS,
    WORKER_MAX_TOKENS,
)

CALL_DEADLINE_S = 120.0


class WorkerHTTPError(RuntimeError):
    """A specialist worker endpoint answered HTTP 500 (injected or real)."""

    def __init__(self, worker, status=500):
        super().__init__(f"HTTP {status} from {worker}")
        self.worker = worker
        self.status = status


@dataclass
class WorkerResult:
    worker: str
    hop: str
    payload: dict = None
    status: str = "not_started"   # ok | http_500 | tool_not_allowed | invalid_json |
    #                      max_calls | budget | llm_error
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens: int = 0
    cost: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 0
    steps: list = field(default_factory=list)
    calls: list = field(default_factory=list)   # hand-off records (one per model call)

    @property
    def ok(self):
        return self.status == "ok" and isinstance(self.payload, dict)

    def to_dict(self):
        return {
            "worker": self.worker, "hop": self.hop,
            "payload": self.payload, "status": self.status, "error": self.error,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.tokens, "cost": round(self.cost, 6),
            "latency_ms": round(self.latency_ms, 1), "attempts": self.attempts,
            "steps": self.steps, "calls": self.calls,
        }


COVERAGE_SYSTEM = """You are the COVERAGE / EXCLUSIONS specialist in a three-agent insurance-claims team.
Manager (orchestrator) -> you -> manager. You never talk to the other specialist.

YOUR ONE NARROW JOB: for the single claim slice you receive, decide whether the policy
covers the loss and whether a policy exclusion clause applies. You do NOT summarise the
adjuster notes, you do NOT compute payouts, you do NOT decide the final claim status.

TOOLS - you have EXACTLY these two, nothing else:
- search_policy(query): search the policy wording, returns up to 3 passages (page, section, snippet).
- retrieve_policy_exclusions(topic): exclusion-tuned search, returns up to 3 exclusion passages.
You do NOT have get_claim, compute_payout, validate_claim_number or escalate_claim.
The claim slice (id, title, adjuster notes) is in the user message; you never fetch it.

OUTPUT FORMAT (strict JSON, one object per message):
- To call a tool:  {"action": {"tool": "<name>", "args": {...}}}
- To finish:       {"finding": {
                      "coverage_question": "<the question you answered>",
                      "policy_evidence": [{"section": "...", "page_number": 0, "snippet": "..."}],
                      "exclusion_clause": "Exc-X.Y" or null,
                      "coverage_verdict": "covered" | "excluded" | "unclear",
                      "reasoning_summary": "<one sentence>"}}

HARD RULES:
- Base the finding ONLY on the adjuster notes slice and on passages a tool actually returned.
- Set exclusion_clause ONLY to a clause id that appears in a returned passage or in the
  notes slice. Never invent a clause id.
- If no passage supports an exclusion, set coverage_verdict to "covered" or "unclear" and
  exclusion_clause to null - never guess an exclusion.
- At most TOOLS_ALLOWED tool calls, then you MUST emit the finding object.
"""

SUMMARY_SYSTEM = """You are the ADJUSTER-NOTE SUMMARY specialist in a three-agent insurance-claims team.
Manager (orchestrator) -> you -> manager. You never talk to the other specialist.

YOUR ONE NARROW JOB: read the single claim slice you receive and extract its key facts.
You do NOT search the policy, you do NOT decide coverage, you do NOT compute payouts.

TOOLS - you have EXACTLY these two, nothing else:
- get_claim(claim_id): retrieve the structured claim record.
- validate_claim_number(claim_number): read-only format check (CLM-YYYY-NNNNN).
You do NOT have search_policy, retrieve_policy_exclusions, compute_payout or escalate_claim.

OUTPUT FORMAT (strict JSON, one object per message):
- To call a tool:  {"action": {"tool": "<name>", "args": {...}}}
- To finish:       {"finding": {
                      "claim_number": "...", "loss_date": "YYYY-MM-DD" or null,
                      "excess": <number or null>,
                      "key_facts": ["<fact>", ...],
                      "denial_language": true | false,
                      "partial_approval": true | false,
                      "exclusion_mentioned": "Exc-X.Y" or null,
                      "summary": "<2-3 sentence faithful summary>"}}

HARD RULES:
- Copy claim_number, loss_date and excess verbatim from the get_claim result or from the
  notes slice; never invent a value.
- denial_language is true only when the notes actually recommend denial / say excluded /
  not payable.
- partial_approval is true when the notes recommend paying part of the claim while
  excluding another part (or approving subject to a condition) WITHOUT recommending
  denial of the whole claim. A partial approval is still an approval.
- exclusion_mentioned is the clause id the notes literally cite, or null.
- At most 2 tool calls, then you MUST emit the finding object.
"""


def _llm_call(messages, *, max_tokens, case, hop, call_index, detail,
              wall_deadline=CALL_DEADLINE_S):
    """One model call billed to a hand-off; returns (content, record)."""
    t0 = time.perf_counter()
    content, usage = llm.chat(
        messages, temperature=0.0, max_tokens=max_tokens,
        wall_deadline=wall_deadline,
    )
    latency_ms = (time.perf_counter() - t0) * 1000.0
    cost = llm.token_cost(usage)
    rec = make_call(case, hop, call_index, 200, usage["prompt_tokens"],
                    usage["completion_tokens"], cost, latency_ms, detail)
    return content, rec, usage, cost


def run_worker(worker_name, hop, system_prompt, user_payload, allowed_tools,
               case, *, finding_key="finding", inject_500=False,
               max_calls=WORKER_MAX_LLM_CALLS, max_tokens=WORKER_MAX_TOKENS,
               call_max_tokens=700, wall_deadline=None):
    """Run one narrow specialist on one claim slice.

    ``wall_deadline`` (seconds remaining on the manager's wall-clock budget) is
    handed to every model call, exactly like the single agent does - a worker
    must be allowed to ride out a 429 for as long as the team's budget allows,
    otherwise the race measures the harness, not the pattern.

    Raises WorkerHTTPError when ``inject_500`` is set - the failure surfaces at
    the worker boundary, before any model usage is billed, which is where a
    real HTTP 500 from a worker endpoint would surface.
    """
    if inject_500:
        raise WorkerHTTPError(worker_name, 500)

    res = WorkerResult(worker=worker_name, hop=hop)
    start = time.perf_counter()
    if wall_deadline is None:
        wall_deadline = CALL_DEADLINE_S
    system_prompt = system_prompt.replace("TOOLS_ALLOWED", str(len(allowed_tools)))
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]

    try:
        for call_index in range(1, max_calls + 1):
            if res.tokens >= max_tokens:
                res.status, res.error = "budget", (
                    f"worker token budget hit: {res.tokens} >= {max_tokens}")
                break

            remaining = wall_deadline - (time.perf_counter() - start)
            if remaining <= 0:
                res.status, res.error = "budget", (
                    f"wall-clock budget exhausted before call {call_index} could start")
                break

            content, rec, usage, cost = _llm_call(
                messages, max_tokens=call_max_tokens, case=case, hop=hop,
                call_index=call_index, detail="worker model call",
                wall_deadline=max(remaining, 1.0))
            res.calls.append(rec)
            res.prompt_tokens += usage["prompt_tokens"]
            res.completion_tokens += usage["completion_tokens"]
            res.tokens += usage["total_tokens"]
            res.cost += cost

            parsed = parse_tool_json(content)
            if not isinstance(parsed, dict):
                res.status, res.error = "invalid_json", "worker output was not JSON"
                break

            if "action" in parsed:
                action = parsed["action"] if isinstance(parsed["action"], dict) else {}
                tool = action.get("tool") or action.get("tool_name")
                args = action.get("args") or action.get("arguments") or {}
                if tool not in allowed_tools:
                    res.status, res.error = "tool_not_allowed", (
                        f"{worker_name} asked for {tool!r}; allow-list is {allowed_tools}")
                    res.steps.append({"tool": tool, "args": args, "status": "blocked"})
                    break
                try:
                    result = call_tool(tool, args if isinstance(args, dict) else {})
                    step_status = "ok"
                except Exception as exc:  # noqa: BLE001
                    result = {"error": str(exc)}
                    step_status = "error"
                res.steps.append({"tool": tool, "args": args, "status": step_status})
                messages.append({"role": "assistant", "content": json.dumps(parsed)})
                messages.append({"role": "user", "content": json.dumps(
                    {"tool_result": {"tool": tool, "status": step_status,
                                     "result": result}}, ensure_ascii=False)})
                res.calls[-1]["detail"] = f"tool={tool} status={step_status}"
                time.sleep(CALL_PACE_S)
                continue

            if finding_key in parsed and isinstance(parsed[finding_key], dict):
                res.payload = parsed[finding_key]
                res.status = "ok"
                res.calls[-1]["detail"] = "finding emitted"
                break

            res.status, res.error = "invalid_json", (
                f"worker output had neither action nor {finding_key}")
            break
        else:
            res.status, res.error = "max_calls", (
                f"worker used all {max_calls} model calls without emitting a finding")
    except llm.WallBudgetExceeded as exc:
        res.status, res.error = "budget", str(exc)
    except Exception as exc:  # noqa: BLE001
        res.status, res.error = "llm_error", str(exc)

    res.latency_ms = (time.perf_counter() - start) * 1000.0
    return res


def run_coverage_worker(case, claim_slice, *, inject_500=False, wall_deadline=None):
    """Coverage/exclusions specialist - narrow tools, narrow slice."""
    payload = {
        "claim_id": claim_slice.get("claim_id"),
        "title": claim_slice.get("title"),
        "adjuster_notes": claim_slice.get("adjuster_notes"),
        "question": ("Does the policy cover this loss, and does a policy exclusion "
                     "clause apply? Quote the clause id if one applies."),
    }
    return run_worker(
        "coverage_exclusions_worker", "orchestrator->coverage_worker",
        COVERAGE_SYSTEM, payload, COVERAGE_WORKER_TOOLS, case,
        inject_500=inject_500, wall_deadline=wall_deadline,
    )


def run_summary_worker(case, claim_slice, *, inject_500=False, wall_deadline=None):
    """Adjuster-note summary specialist - narrow tools, narrow slice."""
    payload = {
        "claim_id": claim_slice.get("claim_id"),
        "title": claim_slice.get("title"),
        "adjuster_notes": claim_slice.get("adjuster_notes"),
        "question": "Extract the key facts of this claim for the manager.",
    }
    return run_worker(
        "adjuster_note_summary_worker", "orchestrator->summary_worker",
        SUMMARY_SYSTEM, payload, SUMMARY_WORKER_TOOLS, case,
        inject_500=inject_500, wall_deadline=wall_deadline,
    )
