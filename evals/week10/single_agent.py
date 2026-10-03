"""The single-agent arm: week 7's ReAct claims agent, run unchanged.

This is "the single agent you already had" - same prompt, same six tools, same
loop, same budgets, same model.  Only one thing moves: the log directory, so
week-10 runs land in evals/week10/logs/ instead of overwriting week 7's logs.
"""

import sys
from pathlib import Path

_WEEK7 = Path(__file__).resolve().parent.parent / "week7"
if str(_WEEK7) not in sys.path:
    sys.path.insert(0, str(_WEEK7))

import agent as week7_agent                       # noqa: E402  (week-7 stack)
from race_config import LOG_DIR                   # noqa: E402

SYSTEM_NAME = "single_agent"


def run_single(claim_id, budgets=None, expected=None):
    """Run the single agent on one claim (week-7 AgentRun record)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    week7_agent.LOG_DIR = LOG_DIR
    if budgets is None:
        budgets = week7_agent.Budgets.from_config()
    run = week7_agent.run_agent(claim_id, budgets=budgets, expected=expected)
    return run


def to_record(run):
    """Cache/JSON form of a single-agent run (no hand-offs: it has none)."""
    return {
        "claim_id": run.claim_id,
        "system": SYSTEM_NAME,
        "pass_result": run.pass_result,
        "latency_ms": round(run.latency_ms, 1),
        "tokens": run.tokens,
        "prompt_tokens": run.prompt_tokens,
        "completion_tokens": run.completion_tokens,
        "cost": round(run.cost, 6),
        "termination_reason": run.termination_reason,
        "termination_detail": run.termination_detail,
        "iterations": run.iterations,
        "output": run.output,
        "failures": run.failures,
        "steps": run.steps,
        "handoffs": [],
        "model": run.model,
        "degraded": False,
        "guardrail_fired": False,
        "worker_attempts": {},
        "injected_failure": False,
    }
