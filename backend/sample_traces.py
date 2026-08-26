import argparse
import json
import random
from pathlib import Path

import trace_log

EVALS_DIR = Path(__file__).resolve().parent.parent / "evals"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, required=True,
                        help="Seed value; paste it in notes.md")
    parser.add_argument("--count", type=int, default=20,
                        help="How many traces to draw (default 20)")
    parser.add_argument("--demo", action="store_true",
                        help="Sample from traces_demo/ (bonus) instead of traces/")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not write the evals/sample_*.json record")
    args = parser.parse_args()

    directory = trace_log.DEMO_TRACE_DIR if args.demo else trace_log.TRACE_DIR
    pool = trace_log.list_trace_ids(directory)

    if not pool:
        raise SystemExit(
            f"No traces found in {directory}. Run the app or "
            f"run_trace_batch.py first."
        )
    if args.count > len(pool):
        raise SystemExit(
            f"Requested {args.count} but only {len(pool)} traces exist in {directory}."
        )

    rng = random.Random(args.seed)
    selected = sorted(rng.sample(pool, args.count))

    method = f"random.Random({args.seed}).sample(sorted(pool of {len(pool)}), {args.count})"
    label = "DEMO (curated)" if args.demo else "RANDOM"


    if not args.no_save:
        EVALS_DIR.mkdir(exist_ok=True)
        name = "sample_demo.json" if args.demo else "sample_random.json"
        out_path = EVALS_DIR / name
        out_path.write_text(
            json.dumps({
                "label": label,
                "seed": args.seed,
                "method": method,
                "pool_size": len(pool),
                "count": args.count,
                "trace_ids": selected,
            }, indent=2),
            encoding="utf-8",
        )
        print(f"Saved machine-readable copy: {out_path}")


if __name__ == "__main__":
    main()
