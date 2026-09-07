import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WEEK7_DIR = Path(__file__).resolve().parent
RESULTS_DIR = WEEK7_DIR / "results"
LOG_DIR = WEEK7_DIR / "logs"

# Used by prior weeks and by the Groq client.  The race model is the one
# already configured in backend/.env (openai/gpt-oss-120b).
ENV_PATH = REPO_ROOT / "backend" / ".env"

# ---------------------------------------------------------------------------
# Model + pricing
# ---------------------------------------------------------------------------
# The backend's configured model (GROQ_MODEL in backend/.env =
# openai/gpt-oss-120b) is throttled at model level on the shared free-tier key:
# every prompt bigger than a couple of thousand tokens earns a 429 with a
# multi-minute Retry-After, so a 10-claim agent race would starve.  The race
# therefore uses a stable model on the SAME key for BOTH systems
# (openai/gpt-oss-120b is probe-equivalent here when it is contactable, and
# qwen/qwen3.8-27b produces the same correct JSON tool actions).  Both systems
# share ONE model id, satisfying the "same LLM configuration for both" rule,
# and this switch is disclosed in results/.  Override with WEEK7_MODEL.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
WEEK7_MODEL = os.getenv("WEEK7_MODEL") or "qwen/qwen3.8-27b"
MODEL = os.getenv("GROQ_MODEL") or WEEK7_MODEL

# Pricing assumption (documented in the race report): official Groq list price
# for the race model (qwen/qwen3.8-27b), USD per 1M tokens, from
# console.groq.com/docs/model/qwen/qwen3.8-27b (Preview Models table; also
# captured by usagepricing.com on 2026-08-27): input $0.80 / output $4.00.
# Both systems use the SAME pricing table so the comparison is consistent; the
# assumption is identified in the evaluation output.
PRICE_PER_1M_INPUT = 0.80
PRICE_PER_1M_OUTPUT = 4.00
PRICING_SOURCE = (
    "Groq list price for qwen/qwen3.8-27b (console.groq.com/docs/model/"
    "qwen/qwen3.8-27b): input $0.80 / output $4.00 per 1M tokens"
)

LLM_TIMEOUT_S = 120.0
LLM_MAX_ATTEMPTS = 15
LLM_BASE_BACKOFF_S = 4.0
LLM_BACKOFF_MULT = 1.8
LLM_RETRY_429_S = 20.0

# The shared Groq key rate-limits openai/gpt-oss-120b at model level; the agent
# paces its decision calls (a real agent against a rate-limited backend does
# this too) so the token window can refill between steps.  Measured honestly in
# the agent's latency and wall-clock, exactly like throttling in production.
AGENT_CALL_PACE_S = float(os.getenv("WEEK7_AGENT_CALL_PACE_S", "2"))

# ---------------------------------------------------------------------------
# Agent budgets (all four enforced inside the loop; overridable per run)
# ---------------------------------------------------------------------------
MAX_ITERS = int(os.getenv("WEEK7_MAX_ITERS", "8"))
MAX_TOKENS = int(os.getenv("WEEK7_MAX_TOKENS", "20000"))
MAX_COST = float(os.getenv("WEEK7_MAX_COST", "0.05"))
# Wall-clock is generous by default because the openai/gpt-oss-120b reasoning
# backend has high per-call variance (1s-10min); the other three budgets bind
# far sooner.  The mechanism is identical for every budget and is demonstrated
# by the forced small values in the budget-termination test.
# Wall-clock is generous by default because the openai/gpt-oss-120b model on the
# shared Groq key carries a multi-minute model-level cool-off per burst; the
# other budgets bind far sooner.  The mechanism is identical for every budget
# and is demonstrated by the forced small values in the budget-termination test.
MAX_SECONDS = float(os.getenv("WEEK7_MAX_SECONDS", "1800"))

# ---------------------------------------------------------------------------
# Evaluation data / claims
# ---------------------------------------------------------------------------
EVAL_SET_PATH = REPO_ROOT / "evals" / "week6" / "eval_set.jsonl"
if not EVAL_SET_PATH.exists():
    EVAL_SET_PATH = REPO_ROOT / "evals" / "week6" / "cases.jsonl"

# The policy reference corpus: the sample two-wheeler policy already indexed in
# the project (backend/indices/f833ddcd-*.chunks.pkl).  It contains coverage,
# exclusions, deductibles, add-ons and worked claim scenarios.
POLICY_INDEX_PATH = (
    REPO_ROOT
    / "backend"
    / "indices"
    / "f833ddcd-81b2-4260-b46c-d59fb3bca267_chunks.pkl"
)

# LOCAL POLICY Chunk files are used so the race never hits the network/retrieval
# pipeline; search_policy is a deterministic local search.
POLICY_TOP_K = 3

# ---------------------------------------------------------------------------
# Claim-amount assumption
# ---------------------------------------------------------------------------
# The supplied evaluation dataset (evals/week6/eval_set.jsonl) carries
# claim_number, loss_date, excess, denial and adjuster notes but NO claim
# amount.  The claims-triage output contract requires a payable amount, and
# compute_payout needs a numeric claim amount.  This is an unavoidable,
# documented assumption: a fixed claim amount is used for every claim (the
# adjuster notes of the ten selected claims quote no repair estimate).  It is
# configurable so races can re-run under a different assumption.
DEFAULT_CLAIM_AMOUNT = float(os.getenv("WEEK7_CLAIM_AMOUNT", "30000"))

# Selection rule for the 10-claim race set (see claims.py):
# documented deterministic rule, no sampling.
SELECTION_RULE = (
    "Take the 10 claims with the lowest numeric id whose expect.excess is a "
    "positive number (ids W6-001..W6-006, W6-008..W6-011).  This preserves all "
    "six Week-6 failure modes and keeps at least 3 branching (omitted-exclusion) "
    "claims where the policy clause to look up depends on the adjuster notes."
)
RACE_CLAIM_IDS = [
    "W6-001", "W6-002", "W6-003", "W6-004", "W6-005",
    "W6-006", "W6-008", "W6-009", "W6-010", "W6-011",
]

# Fixed query used by the deterministic workflow's single search_policy call.
WORKFLOW_POLICY_QUERY = (
    "own damage coverage, exclusions and deductibles for a two-wheeler"
)