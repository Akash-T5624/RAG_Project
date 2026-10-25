"""One place for every path, so nothing depends on the current directory (seam S1)."""

import os
import sys
from pathlib import Path

CAPSTONE = Path(__file__).resolve().parent.parent
REPO_ROOT = CAPSTONE.parent
BACKEND = REPO_ROOT / "backend"
WEEK7 = REPO_ROOT / "evals" / "week7"
WEEK9 = REPO_ROOT / "week9_mcp"
WEEK6 = REPO_ROOT / "evals" / "week6"
DATA = CAPSTONE / "data"
REPORTS = Path(os.getenv("CAPSTONE_REPORTS_DIR") or CAPSTONE / "reports")
TRACES = REPORTS / "traces"
ENV_PATH = BACKEND / ".env"


def ensure_paths():
    """Make repo root, backend/ and week7/ importable (the earlier weeks use flat imports)."""
    for p in (REPO_ROOT, BACKEND, WEEK7):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


ensure_paths()
