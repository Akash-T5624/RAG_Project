"""backend/trajectory_benchmark.py — API-side adapter around the week-8 eval.

The long-running trajectory eval lives in evals/week8/backend_spec (it needs
the eval harness AND the real retrieval stack).  This module is the bridge the
FastAPI endpoints use: it puts both directories on sys.path and exposes two
functions:

    run_report(doc_id, skip_injection)  -> runs main()'s full pipeline, writes
                                           and returns the nested report dict
    load_last_report()                  -> returns the last saved report, or
                                           None if none exists yet
"""

import asyncio
import json
import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent
_WEEK8_SPEC = _BACKEND.parent / "evals" / "week8" / "backend_spec"
for _p in (str(_BACKEND), str(_WEEK8_SPEC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPORT_PATH = _WEEK8_SPEC / "results" / "trajectory_report.json"


def run_report(doc_id=None, skip_injection=False):
    """Run the full baseline -> mitigation -> regression -> injection pipeline.

    Mirrors race.py / trajectory_eval.py's convention: the caller (an API
    endpoint) is responsible for holding the long-run lock; this just executes
    the eval in-process and returns the report dict.
    """
    import main as trajectory_main

    return asyncio.run(trajectory_main._run_all(
        doc_id, skip_injection=skip_injection))


def load_last_report():
    """Return the last saved trajectory_report.json, or None."""
    if not REPORT_PATH.exists():
        return None
    try:
        with open(REPORT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return None