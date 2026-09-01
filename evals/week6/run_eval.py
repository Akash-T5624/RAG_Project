import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Windows UTF-8 console
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(HERE))

import assertions as assertions_mod
import summary_gen
import judge as judge_mod
import regression as regression_mod


def load_cases() -> dict:
    cases = {}
    path = HERE / "eval_set.jsonl"
    if not path.exists():
        path = HERE / "cases.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        cases[obj["id"]] = obj
    return cases


def load_labels() -> dict:
    path = HERE / "labels_25.json"
    if not path.exists():
        raise SystemExit(
            "labels_25.json not found. It must exist (written blind) BEFORE the "
            "judge runs — this is the ordering the rubric checks."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return {c["id"]: c["hand_label"] for c in data["labels"]}


def build_mode_table(cases, summaries):
    """Run deterministic assertions for every case; aggregate pass rate by mode."""
    per_case = {}
    for cid, case in cases.items():
        res = assertions_mod.run_assertions(summaries[cid], case["expect"])
        per_case[cid] = res

    # Aggregate by mode
    by_mode = {}
    for cid, case in cases.items():
        mode = case["mode"]
        by_mode.setdefault(mode, {"pass": 0, "total": 0, "failing": []})
        by_mode[mode]["total"] += 1
        if per_case[cid]["all_pass"]:
            by_mode[mode]["pass"] += 1
        else:
            by_mode[mode]["failing"].append(cid)

    return by_mode, per_case


def print_mode_table(by_mode):
    print("\n=== PASS RATE BY MODE (deterministic assertions) ===")
    header = f"{'Mode':<28}{'Pass':>6}{'Total':>7}{'Pass%':>8}"
    print(header)
    print("-" * len(header))
    total_p = total_t = 0
    for mode in sorted(by_mode):
        p, t = by_mode[mode]["pass"], by_mode[mode]["total"]
        total_p += p
        total_t += t
        label = mode + ("   <-- failing cases!" if by_mode[mode]["failing"] else "")
        print(f"{label:<28}{p:>6}{t:>7}{p / t * 100:>7.1f}%")
    print("-" * len(header))
    print(f"{'TOTAL':<28}{total_p:>6}{total_t:>7}{total_p / total_t * 100:>7.1f}%")
    failing = {m: by_mode[m]["failing"] for m in by_mode if by_mode[m]["failing"]}
    if failing:
        print("\nFailing cases by mode:")
        for m, ids in failing.items():
            print(f"  {m}: {', '.join(ids)}")
    print(f"\nAssertion criteria count: {len(assertions_mod.ASSERTION_NAMES)} "
          f"(deterministic)")
    print(f"Judged criteria count: 1 (binary faithfulness/completeness)")


def compute_agreement(verdicts: dict, labels: dict) -> dict:
    agree = 0
    total = 0
    details = {}
    for cid in labels:
        human = labels[cid]
        judge_v = verdicts.get(cid, {}).get("verdict")
        # Map judge ACCEPT -> acceptable, REJECT -> reject
        if judge_v in ("ACCEPT", "REJECT"):
            j = "acceptable" if judge_v == "ACCEPT" else "reject"
            match = (j == human)
            total += 1
            agree += 1 if match else 0
            details[cid] = {"human": human, "judge": j, "match": match, "judge_raw": judge_v}
        else:
            details[cid] = {"human": human, "judge": judge_v, "match": False, "judge_raw": judge_v}
            total += 1
    pct = (agree / total * 100) if total else 0.0
    return {"agree": agree, "total": total, "pct": round(pct, 1), "details": details}


def disagreements(comp: dict) -> list:
    return [cid for cid, d in comp["details"].items() if not d["match"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true",
                        help="Regenerate summaries + judge verdicts from Groq")
    parser.add_argument("--regenerate-judge", action="store_true",
                        help="Regenerate only judge verdicts")
    parser.add_argument("--regenerate-regression", action="store_true",
                        help="Re-replay the real regression traces")
    parser.add_argument("--skip-hierarchical", action="store_true",
                        help="Skip the before/after hierarchical retrieval run")
    args = parser.parse_args()

    print("=" * 50)
    print("INSURANCE RAG WEEK 6 EVALUATION")
    print("=" * 50)
    print()

    cases = load_cases()
    labels = load_labels()
    print(f"Total cases: {len(cases)}")
    print(f"Human labels (blind): {len(labels)}")
    n_reg = 0
    reg_cases_path = HERE / "regression_cases.jsonl"
    if reg_cases_path.exists():
        n_reg = sum(1 for l in reg_cases_path.read_text(encoding="utf-8").splitlines()
                    if l.strip())
    print(f"Regression cases: {n_reg}")
    print()

    # --- Generate summaries ---
    sum_path = HERE / "generated_summaries.json"
    if args.regenerate or not sum_path.exists():
        print("Generating claim summaries (cached -> generated_summaries.json)...")
        summaries = summary_gen.generate_all(HERE / "cases.jsonl", sum_path, force=args.regenerate)
    else:
        summaries = json.loads(sum_path.read_text(encoding="utf-8"))
        print(f"Using cached summaries ({len(summaries)}).")

    # --- Deterministic assertions + mode table ---
    by_mode, per_case = build_mode_table(cases, summaries)
    print_mode_table(by_mode)

    # --- Judge v1 (before) ---
    v1_path = HERE / "judge_v1_results.json"
    print("\nRunning judge v1 (before iteration)...")
    v1 = judge_mod.run_judge_all(
        cases, summaries, "v1", v1_path,
        force=args.regenerate or args.regenerate_judge,
    )
    comp_v1 = compute_agreement(v1, labels)
    print(f"Agreement (v1) = {comp_v1['agree']}/{comp_v1['total']} "
          f"= {comp_v1['pct']}%")

    # --- Judge v2 (after iteration) ---
    v2_path = HERE / "judge_v2_results.json"
    prediction_path = HERE / "prediction.txt"
    if prediction_path.exists():
        print("\nRunning judge v2 (after iteration, driven by v1's own disagreements)...")
        v2 = judge_mod.run_judge_all(
            cases, summaries, "v2", v2_path,
            force=args.regenerate or args.regenerate_judge,
        )
        comp_v2 = compute_agreement(v2, labels)
        print(f"Agreement (v2) = {comp_v2['agree']}/{comp_v2['total']} "
              f"= {comp_v2['pct']}%")
        print(f"\nAgreement BEFORE -> AFTER: {comp_v1['pct']}% -> {comp_v2['pct']}%")
    else:
        comp_v2 = None
        print("\nprediction.txt not present — skipping judge v2 (after) phase.")
        print("Write prediction.txt first, then re-run to get the after number.")
        print("Agreement BEFORE -> AFTER: "
              f"{comp_v1['pct']}% -> (pending judge_v2 + prediction)")

    # --- Disagreement summary ---
    dis_v1 = disagreements(comp_v1)
    print(f"\nDisagreements with human labels (v1): {dis_v1 or 'none'}")
    if comp_v2 is not None:
        dis_v2 = disagreements(comp_v2)
        print(f"Disagreements with human labels (v2): {dis_v2 or 'none'}")

    # --- Regression tests from real failed traces ---
    print("\n=== REGRESSION TESTS (replayed verbatim from real failed traces) ===")
    reg_path = HERE / "regression_results.json"
    reg = regression_mod.run_regression(
        reg_path, force=args.regenerate or args.regenerate_regression,
    )
    reg_pass = sum(1 for r in reg if r["passed"])
    for r in reg:
        print(f"  {r['trace_id'][:8]} ({r['question'][:30]}...): "
              f"{'PASS' if r['passed'] else 'FAIL'} — {r['detail']}")
    print(f"Regression pass rate: {reg_pass}/{len(reg)}")

    # --- Final rubric summary ---
    print()
    print("=" * 50)
    print("WEEK 6 SUMMARY (rubric-oriented)")
    print("=" * 50)
    print()
    print(f"Deterministic assertion criteria: {len(assertions_mod.ASSERTION_NAMES)}")
    for i, name in enumerate(assertions_mod.ASSERTION_NAMES, 1):
        print(f"  {i}. {name}")
    print(f"LLM judged criteria: 1")
    print("  1. summary accurately represents adjuster notes (binary)"
          " - faith./completeness only")
    print()
    print(f"Agreement BEFORE (judge v1 vs blind labels): {comp_v1['pct']}%")
    if comp_v2 is not None:
        delta = round(comp_v2["pct"] - comp_v1["pct"], 1)
        print(f"Agreement AFTER  (judge v2 vs blind labels): {comp_v2['pct']}%")
        print(f"Delta: {delta:+} percentage points")
    print(f"Regression pass rate: {reg_pass}/{len(reg)}")

    if not args.skip_hierarchical:
        print()
        import hierarchical as hier_mod
        hier_mod.main()

    print()
    print("=" * 50)
    print("END OF EVALUATION")
    print("=" * 50)


if __name__ == "__main__":
    main()
