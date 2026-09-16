"""trajectory_runner.py — run one labelled case and collect its trace.

The per-step list is rebuilt from the agent's event stream.  Event-order bug
guarded here (verified live): when the agent emits a "reasoning done" event
BEFORE the "action start" event for the same step (very common), we must NOT
write the reason text straight onto steps[-1] when the reason event arrives —
at that moment steps[-1] is still the PREVIOUS step, because the current step
hasn't been created yet.  We park the reason in a pending variable and attach
it when the step actually gets created:

    pending_reason = ""
    async for event in engine.run_stream(request):
        if event["type"] == "agent.reason" and event["data"].get("status") == "done":
            pending_reason = event["data"].get("reason", "")
        elif event["type"] == "agent.action.start":
            steps.append({..., "reason": pending_reason, ...})
            pending_reason = ""
        elif event["type"] == "agent.observation":
            if steps: steps[-1]["observation"] = event["data"].get("output")
        elif event["type"] == "trace.completed":
            answer = event["data"].get("answer", "")
            # ...pull tokens / latency / cost from the completed event...

Before the fix, reason fields were shifted by one step and the last step
always had reason: None.
"""

import asyncio
import time

from trajectory_cases import TRAJECTORY_CASES
from trajectory_metrics import process_case_metrics

from agent_engine import AGENT_CASE_PACE_S


def build_steps_from_events(events):
    """Rebuild the ordered step list from the raw event stream.

    Returns (steps, answer, error, tokens, latency_ms, cost).
    Step shape: {
        "action": {"tool": ..., "args": {...}},
        "reason": <the reasoning that preceded this action>,
        "observation": <the tool's return value (already sanitized)>,
    }
    """
    steps = []
    pending_reason = ""
    answer = None
    error = None
    tokens = 0
    latency_ms = 0.0
    cost = 0.0

    for event in events:
        etype = event.get("type")
        data = event.get("data", {}) or {}

        if etype == "agent.reason" and data.get("status") == "done":
            pending_reason = data.get("reason", "")

        elif etype == "agent.action.start":
            steps.append({
                "action": {"tool": data.get("tool"),
                           "args": data.get("args", {}) or {}},
                "reason": pending_reason,
                "observation": None,
            })
            pending_reason = ""

        elif etype == "agent.observation":
            if steps:
                steps[-1]["observation"] = data.get("output")

        elif etype == "trace.completed":
            answer = data.get("answer")
            tokens = int(data.get("tokens", 0) or 0)
            latency_ms = float(data.get("latency_ms", 0.0) or 0.0)
            cost = float(data.get("cost", 0.0) or 0.0)
            error = data.get("error")

    return steps, answer, error, tokens, latency_ms, cost


async def run_case(case, engine, doc_id=None):
    """Run one trajectory case through an already-configured AgentEngine.

    Returns the per-case metrics dict (see trajectory_metrics.process_case_metrics).
    """
    request = {"question": case["question"], "doc_id": doc_id}
    events = []
    async for event in engine.run_stream(request):
        events.append(event)

    steps, answer, error, tokens, latency_ms, cost = build_steps_from_events(events)
    return process_case_metrics(
        case, steps, answer,
        tokens=tokens, latency_ms=latency_ms, cost=cost,
        error=error,
    )


async def run_all_cases(engine_factory, doc_id=None, case_ids=None,
                        progress=None):
    """Run every case with a fresh engine per case from engine_factory.

    engine_factory() must return a configured AgentEngine (this is where the
    mitigation toggle gets set — before/after runs call it differently).
    progress(case_id, metrics_or_None) is invoked after each completed case.
    """
    cases = [c for c in TRAJECTORY_CASES
             if case_ids is None or c["id"] in case_ids]
    results = []
    for i, case in enumerate(cases):
        if i > 0:
            time.sleep(AGENT_CASE_PACE_S)
        engine = engine_factory()
        metrics = await run_case(case, engine, doc_id=doc_id)
        results.append(metrics)
        if progress:
            progress(case["id"], metrics)
    return results


def run_cases_blocking(engine_factory, doc_id=None, case_ids=None):
    """Synchronous wrapper around run_all_cases for console/CLI use."""
    return asyncio.run(run_all_cases(engine_factory, doc_id=doc_id,
                                     case_ids=case_ids))