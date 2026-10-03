"""Run the whole Week-10 deliverable in order:

    1. race.py            both arms, same 10 cases, same judge -> race_table.md
                          + handoffs.log + multiplier.txt
    2. failure_case.py    one injected HTTP 500      -> failure_case.md
    3. verdict.py         keep/kill on the 4 numbers -> verdict.md (max 10 lines)
    4. a2a_bonus.py       AgentCard + task lifecycle  -> a2a_bonus.md

Usage:  cd evals/week10 && python run_all.py [--nocache]
"""

import argparse
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Week 10 build: race, failure, verdict, A2A")
    parser.add_argument("--nocache", action="store_true",
                        help="re-run every claim instead of using results/run_cache.json")
    parser.add_argument("--skip-race", action="store_true",
                        help="reuse cached race results (failure/verdict/A2A only)")
    args = parser.parse_args(argv)

    import failure_case
    import race
    import verdict
    import a2a_bonus

    if not args.skip_race:
        rc = race.main(["--nocache"] if args.nocache else [])
        if rc:
            return rc
    rc = failure_case.main([])
    if rc:
        return rc
    rc = verdict.main([])
    if rc:
        return rc
    rc = a2a_bonus.main([])
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
