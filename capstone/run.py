"""THE entry point. Everything the capstone does starts here.

    python capstone/run.py all                      # one command: init, tests, offline checks, then the live suite if a key exists
    python capstone/run.py triage --claim C-S1      # one FNOL end to end (live model + live MCP server)
    python capstone/run.py triage --fnol "..." --policy MOT-1004 --loss-date 2024-08-11 --product motor
    python capstone/run.py serve-mcp [--http]       # run the MCP server on its own
    python capstone/run.py eval [--before] [--ids A B] [--fresh]
    python capstone/run.py judge | label-sheet | report | loop | cost [--tpm N]
    python capstone/run.py trace <trace_id> [--replay]
    python capstone/run.py demo                     # Part 1 of the demo: a live run, printed for the room
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capstone.core import paths  # noqa: E402,F401
from capstone.core.paths import CAPSTONE, REPORTS, REPO_ROOT  # noqa: E402


def cmd_init(_a):
    subprocess.run([sys.executable, str(CAPSTONE / "data" / "build_fixtures.py")], check=True)
    from capstone.core import retriever
    print("wording pages:", retriever.render_wording_pages())


def cmd_test(_a):
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "capstone/tests", "-t", ".", "-p", "test_*.py"],
                          cwd=REPO_ROOT).returncode


def _render(d):
    from capstone.checks.judge import briefing
    return briefing(d)


def cmd_triage(a):
    from capstone.agent.loop import run_agent
    from capstone.core import llm, policies
    from capstone.mcp_layer.mcp_client import McpSession
    if not llm.api_key_present():
        raise SystemExit("GROQ_API_KEY missing: copy backend/.env.example to backend/.env and set it.")
    claim = a.claim
    if a.fnol:
        claim = "ADHOC-1"
        policies.register_case({"id": claim, "claim_number": "CLM-ADHOC", "product": a.product, "policy_number": a.policy,
                                "loss_date": a.loss_date, "fnol": a.fnol, "slices": [], "pii": {}, "expect": {}})
    mcp = McpSession()
    mcp.start()
    try:
        run = run_agent(claim, mcp)
    finally:
        mcp.close()
    print(f"\ntermination: {run.termination} {run.detail}\ntrace id: {run.trace.trace_id}   cost: ${run.cost:.5f}\n")
    print("tool calls:", " -> ".join(run.tools_called))
    if run.delivered:
        print("\n" + _render(run.delivered))
        print("\n" + json.dumps(run.delivered, indent=1, ensure_ascii=False))


def cmd_serve(a):
    subprocess.run([sys.executable, str(CAPSTONE / "mcp_layer" / "policy_server.py"), *(["--http"] if a.http else [])])


def cmd_eval(a):
    from capstone.checks import run_suite
    run_suite.run_cases("before" if a.before else "after", ids=a.ids, fresh=a.fresh)
    from capstone.checks import report
    print("report:", report.build_eval_report())


def cmd_judge(_a):
    from capstone.checks import run_suite
    recs = json.loads(run_suite.path_for("after").read_text(encoding="utf-8"))
    out = run_suite.judge_records(recs)
    (REPORTS / "judge_after.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out["agreement"].items() if k != "details"}, indent=1))


def cmd_label(_a):
    from capstone.checks import judge, run_suite
    from capstone.core import policies
    recs = json.loads(run_suite.path_for("after").read_text(encoding="utf-8"))
    cases = policies.load_cases()
    print("written:", judge.label_sheet({cid: (cases[cid], r) for cid, r in recs.items()}))
    print("now fill capstone/data/hand_labels.json as {\"<case id>\": \"acceptable\"|\"reject\"} BEFORE running `judge`")


def cmd_report(_a):
    from capstone.checks import report
    print(report.build_eval_report())
    print(report.build_cost_plan())


def cmd_cost(a):
    from capstone.checks import report
    print(report.build_cost_plan(tpm_limit=a.tpm))


def cmd_loop(a):
    from capstone.checks import loop_demo
    loop_demo.main(a.trace)


def cmd_trace(a):
    from capstone.core import retriever, tracing
    t = tracing.load(a.trace_id)
    print(f"trace {t['trace_id']}  claim {t['session_id']}  model {t['model']}  latency {t['latency_ms']} ms  cost ${t['cost_usd']['total']}")
    print(f"{'span':42} {'hop':11} {'start ms':>9} {'dur ms':>9} {'cost $':>10}")
    for s in t["spans"]:
        print(f"{s['name']:42} {(s['detail'] or {}).get('hop', ''):11} {s['start_ms']:9.0f} {s['duration_ms']:9.0f} {(s['cost_usd'] or 0):10.6f}")
    cap = t["capstone"]
    print("\nwording in force used:", [(w["clause_id"], w["endorsement_version"], w["effective_from"], w["effective_to"]) for w in cap["wording"]])
    print("trace id echoed by MCP server on every hop:", cap["integrity"])
    if a.replay:
        os.environ.setdefault("CAPSTONE_RANKER", os.getenv("CAPSTONE_RANKER", ""))
        for call in cap["search_calls"]:
            sent = call["sent"]
            res = retriever.search_wording(sent["query"], sent["product"], sent["loss_date"], date_filter=call.get("date_filter", True))
            again = [h["chunk_id"] for h in res["hits"]]
            print(f"replay {call['tool']}: {'MATCH' if again == call['returned'] else 'DIFF'}  stored={call['returned']} replayed={again}")
        print("note: retrieval replays from the trace; the model's wording is not guaranteed to repeat.")


def cmd_demo(a):
    a.claim, a.fnol = a.claim or "C-S1", None
    cmd_triage(a)


def cmd_all(a):
    cmd_init(a)
    rc = cmd_test(a)
    print("\n== loss-date retrieval check (no model) ==")
    from capstone.checks import loop_demo, retrieval_eval
    print(json.dumps(retrieval_eval.summarize(retrieval_eval.run()), indent=1))
    print("\n== closed failure->test loop ==")
    loop_demo.main()
    from capstone.core import llm
    if llm.api_key_present():
        a.before, a.ids, a.fresh = False, None, False
        cmd_eval(a)
    else:
        print("\nGROQ_API_KEY not set: skipping the live agent/MCP suite (it is reported as NOT RUN, not estimated).")
        from capstone.checks import report
        print("report:", report.build_eval_report())
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("init", "test", "judge", "label-sheet", "report", "all"):
        sub.add_parser(name)
    t = sub.add_parser("triage")
    t.add_argument("--claim")
    t.add_argument("--fnol")
    t.add_argument("--policy")
    t.add_argument("--loss-date")
    t.add_argument("--product", default="motor")
    sub.add_parser("demo").add_argument("--claim")
    s = sub.add_parser("serve-mcp")
    s.add_argument("--http", action="store_true")
    e = sub.add_parser("eval")
    e.add_argument("--before", action="store_true")
    e.add_argument("--ids", nargs="*")
    e.add_argument("--fresh", action="store_true")
    sub.add_parser("loop").add_argument("--trace")
    sub.add_parser("cost").add_argument("--tpm", type=int)
    tr = sub.add_parser("trace")
    tr.add_argument("trace_id")
    tr.add_argument("--replay", action="store_true")
    a = ap.parse_args(argv)
    fn = {"init": cmd_init, "test": cmd_test, "triage": cmd_triage, "serve-mcp": cmd_serve, "eval": cmd_eval, "judge": cmd_judge,
          "label-sheet": cmd_label, "report": cmd_report, "cost": cmd_cost, "loop": cmd_loop, "trace": cmd_trace,
          "demo": lambda x: cmd_demo(_demo_args(x)), "all": cmd_all}[a.cmd]
    if a.cmd == "triage" and not (a.claim or a.fnol):
        ap.error("triage needs --claim or --fnol")
    return fn(a) or 0


def _demo_args(a):
    for k, v in (("fnol", None), ("policy", None), ("loss_date", None), ("product", "motor")):
        setattr(a, k, getattr(a, k, v))
    return a


if __name__ == "__main__":
    raise SystemExit(main())
