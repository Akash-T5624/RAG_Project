import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "evals" / "week7"))

from test_budget import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())