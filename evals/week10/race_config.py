"""Week 10 / Module 5 configuration - orchestrator (claims squad) vs single agent.

Named ``race_config`` (not ``config``) because week 7's flat module is already
called ``config`` and both directories sit on ``sys.path``; every week-*N*
module imports week 7's stack through the sys.path hook below, exactly like
week 8's ``trajectory_config`` does.

Nothing in week 7 is modified: same model, same pricing, same tools, same 10
eval cases, same judge.  Week 10 only adds the team arm and its measurements.
"""

import os
import sys
from pathlib import Path

WEEK10_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEEK10_DIR.parent.parent
RESULTS_DIR = WEEK10_DIR / "results"
LOG_DIR = WEEK10_DIR / "logs"
HANDOFF_LOG_PATH = RESULTS_DIR / "handoffs.log"
RUN_CACHE_PATH = RESULTS_DIR / "run_cache.json"
MULTIPLIER_PATH = RESULTS_DIR / "multiplier.txt"

_WEEK7 = WEEK10_DIR.parent / "week7"
if str(_WEEK7) not in sys.path:
    sys.path.insert(0, str(_WEEK7))

import config as week7_config  # noqa: E402  (week-7 stack, untouched)

# ---------------------------------------------------------------------------
# Model + pricing (inherited, unchanged from week 7 / week 8)
# ---------------------------------------------------------------------------
# Both arms run on ONE model id on ONE key.  The backend's configured
# openai/gpt-oss-120b is model-level throttled on the shared free-tier key, so
# the week-7 race (and this one) uses qwen/qwen3.8-27b for both systems.
MODEL = os.getenv("WEEK10_MODEL") or week7_config.MODEL
PRICE_PER_1M_INPUT = week7_config.PRICE_PER_1M_INPUT
PRICE_PER_1M_OUTPUT = week7_config.PRICE_PER_1M_OUTPUT
PRICING_SOURCE = week7_config.PRICING_SOURCE

# Inter-call pacing is applied to BOTH arms (a real agent against a
# rate-limited backend paces its calls, and the pacing is measured latency).
CALL_PACE_S = float(os.getenv("WEEK10_CALL_PACE_S", str(week7_config.AGENT_CALL_PACE_S)))

# ---------------------------------------------------------------------------
# The SAME 10 Week-6 eval cases, the SAME judge
# ---------------------------------------------------------------------------
# No new cases are written for week 10 - a changed eval set voids the
# comparison.  These are week 7's documented 10-claim selection out of
# evals/week6/eval_set.jsonl (W6-001..006, 008..011).
RACE_CLAIM_IDS = list(week7_config.RACE_CLAIM_IDS)

# The judge for BOTH arms: week 6/7's contract validator, one function, so the
# judge cannot favour either arm.  (Week 6's LLM judge scores free-text
# summaries of adjuster notes; both week-10 arms emit the structured claim
# contract, so the contract validator is the applicable judge.)
JUDGE_NAME = "outputs.validate_output (evals/week7/outputs.py, week-6 contract judge)"
JUDGE_SOURCE = "evals/week7/outputs.py::validate_output, expected outputs from evals/week7/outputs.py::expected_output"

# ---------------------------------------------------------------------------
# Team arm: one manager + two narrow specialists
# ---------------------------------------------------------------------------
# The single agent keeps ALL six tools (it is the agent you already had).  The
# specialists are deliberately narrower - the narrow-prompt / few-tools
# constraint is the only plausible source of a multi-agent win, so removing it
# would test nothing.
COVERAGE_WORKER_TOOLS = ["search_policy", "retrieve_policy_exclusions"]
SUMMARY_WORKER_TOOLS = ["get_claim", "validate_claim_number"]

# A specialist gets 4 model calls: enough for two tool lookups plus a
# self-correction when a tool argument is wrong (probe run 1 showed a worker
# burning its whole 3-call budget on one bad argument), then the finding.
# Still far narrower than the single agent's 8-iteration loop over 6 tools.
WORKER_MAX_LLM_CALLS = int(os.getenv("WEEK10_WORKER_MAX_CALLS", "4"))
WORKER_MAX_TOKENS = int(os.getenv("WEEK10_WORKER_MAX_TOKENS", "6000"))

# Failure injection: the coverage/exclusions worker returns HTTP 500 on every
# attempt for this one case.  Chosen because it is an EXCLUDED claim - the one
# case where skipping the exclusions check flips excluded -> covered.
FAILURE_INJECTION_CASE = os.getenv("WEEK10_FAILURE_CASE", "W6-002")

# Orchestrator failure policy: one retry, then degrade (never guess coverage).
WORKER_MAX_ATTEMPTS = int(os.getenv("WEEK10_WORKER_MAX_ATTEMPTS", "2"))
WORKER_RETRY_BACKOFF_S = float(os.getenv("WEEK10_WORKER_RETRY_BACKOFF_S", "1.0"))

# Orchestrator budget (same four budgets as the single agent's loop, enforced
# the same way: iterations = model calls, tokens, cost, wall clock).  The wall
# clock matches week 7's MAX_SECONDS (1800 s) so neither arm can be accused of
# being handed a smaller time budget than the other.
MAX_ITERS = int(os.getenv("WEEK10_MAX_ITERS", "12"))
MAX_TOKENS = int(os.getenv("WEEK10_MAX_TOKENS", "30000"))
MAX_COST = float(os.getenv("WEEK10_MAX_COST", "0.10"))
MAX_SECONDS = float(os.getenv("WEEK10_MAX_SECONDS", "1800"))
