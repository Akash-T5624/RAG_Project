"""Week 8 module-level configuration.

Reuses the week-7 tooling (same agent, same tools, same model stack) as the
subject under evaluation; only the *evaluation* is new.  The week-7 package
directory is expected on ``sys.path`` so its flat imports (``import outputs``,
``from config import ...``, ...) resolve untouched.
"""

import os
import sys
from pathlib import Path

_WEEK7 = Path(__file__).resolve().parent.parent / "week7"
if str(_WEEK7) not in sys.path:
    sys.path.insert(0, str(_WEEK7))

_WEEK8 = Path(__file__).resolve().parent
REPO_ROOT = _WEEK8.parent.parent
WEEK8_DIR = _WEEK8
RESULTS_DIR = _WEEK8 / "results"
LOG_DIR = _WEEK8 / "logs"

# ---------------------------------------------------------------------------
# Model + pricing (inherited from the week-7 stack; see week7/config.py).
# The shared free-tier key model-level-throttles openai/gpt-oss-120b, so the
# week-7 race used qwen/qwen3.8-27b on the same key.  We keep that choice for
# both baseline and mitigation so the comparison is apples-to-apples.
# ---------------------------------------------------------------------------
WEEK8_MODEL = os.getenv("WEEK8_MODEL") or "qwen/qwen3.8-27b"
MODEL = WEEK8_MODEL

PRICE_PER_1M_INPUT = float(os.getenv("WEEK8_PRICE_INPUT", "0.80"))
PRICE_PER_1M_OUTPUT = float(os.getenv("WEEK8_PRICE_OUTPUT", "4.00"))
PRICING_SOURCE = (
    "Groq list price for qwen/qwen3.8-27b (console.groq.com/docs/model/"
    "qwen/qwen3.8-27b): input $0.80 / output $4.00 per 1M tokens"
)

# ---------------------------------------------------------------------------
# Agent budgets (same four as week 7; all enforced inside the agent loop).
# ---------------------------------------------------------------------------
MAX_ITERS = int(os.getenv("WEEK8_MAX_ITERS", "8"))
MAX_TOKENS = int(os.getenv("WEEK8_MAX_TOKENS", "20000"))
MAX_COST = float(os.getenv("WEEK8_MAX_COST", "0.05"))
MAX_SECONDS = float(os.getenv("WEEK8_MAX_SECONDS", "1800"))

# ---------------------------------------------------------------------------
# Evaluation set: the same 10-claim set the week-7 race used.
# ---------------------------------------------------------------------------
TRAJECTORY_CLAIM_IDS = [
    "W6-001", "W6-002", "W6-003", "W6-004", "W6-005",
    "W6-006", "W6-008", "W6-009", "W6-010", "W6-011",
]

# Branching claims: the adjuster notes *name* an exclusion clause, so the
# agent must open the policy (search_policy) before deciding.  Everything else
# is a clean claim where consulting the policy is legitimate but not required.
BRANCHING_CLAIM_IDS = ["W6-002", "W6-004", "W6-008", "W6-011"]

# The policy corpus is the same local two-wheeler policy the week-7 tools
# search.  It is also used to verify that an exclusion-clause id quoted in a
# final answer is *real* (present in the policy wording), not fluent fiction.
POLICY_INDEX_PATH = (
    REPO_ROOT / "backend" / "indices"
    / "f833ddcd-81b2-4260-b46c-d59fb3bca267_chunks.pkl"
)

# Cache of per-claim trajectory runs so the eval re-runs cheaply.
RUN_CACHE_PATH = RESULTS_DIR / "runs_cache.json"