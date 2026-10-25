"""Offline tests: no Groq key, no torch. Run: python -m unittest discover -s capstone/tests -t .

These test the code that ENFORCES the rules (date filter, guard, PII, citations, trace id, tool
discovery). They never produce eval numbers: the scripted model below is a test double, and
nothing it returns may appear in a report.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ["CAPSTONE_RANKER"] = "bm25"
os.environ["CAPSTONE_REPORTS_DIR"] = tempfile.mkdtemp(prefix="capstone_test_")

from capstone.agent import loop  # noqa: E402
from capstone.core import guard, pii, policies, retriever  # noqa: E402
from capstone.core.paths import CAPSTONE, REPORTS  # noqa: E402


class FakeMcp:
    """In-process stand-in for McpSession (tests only): same tools/list shape, same trace echo."""
    tools = [{"name": "search_policy_wording", "description": "wording search",
              "inputSchema": {"properties": {"query": {"type": "string"}, "product": {"type": "string"},
                                              "loss_date": {"type": "string"}, "trace_id": {"type": "string"}}}}]

    def call(self, name, args):
        r = retriever.search_wording(args["query"], args["product"], args["loss_date"])
        r["_server"] = {"trace_id": args.get("trace_id"), "duration_ms": 1.0}
        return r


def scripted(*messages):
    it = iter(messages)

    def chat(msgs, wall_deadline=None):
        return json.dumps(next(it)), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, 0.0
    return chat


def act(tool, **args):
    return {"action": {"tool": tool, "args": args}}


class DateFilter(unittest.TestCase):
    def test_2024_loss_never_gets_2025_wording(self):
        r = retriever.search_wording("flood water ingress engine", "motor", "2024-08-11")
        self.assertTrue(r["hits"])
        self.assertEqual({h["endorsement_version"] for h in r["hits"]}, {"2023"})

    def test_boundary_days(self):
        v = lambda d: {h["endorsement_version"] for h in retriever.search_wording("flood", "motor", d)["hits"]}
        self.assertEqual(v("2024-12-31"), {"2023"})
        self.assertEqual(v("2025-01-01"), {"2025"})

    def test_no_wording_in_force_is_explicit(self):
        r = retriever.search_wording("burst pipe", "household", "2022-09-01")
        self.assertEqual((r["status"], r["hits"]), ("NO_WORDING_IN_FORCE", []))

    def test_bad_date_is_an_error_not_a_guess(self):
        self.assertEqual(retriever.search_wording("flood", "motor", "sometime in March")["status"], "BAD_DATE")


class InForce(unittest.TestCase):
    def test_lapsed_and_early(self):
        self.assertFalse(policies.check_coverage_in_force("MOT-1003", "2024-06-15")["in_force"])
        self.assertFalse(policies.check_coverage_in_force("MOT-1003", "2023-05-10")["in_force"])
        self.assertTrue(policies.check_coverage_in_force("MOT-1004", "2025-01-01")["in_force"])


class Guard(unittest.TestCase):
    def test_removes_decision_words_but_not_quotes(self):
        out, v = guard.sanitize_output({"referral_reason": "This claim is covered and approved.",
                                        "citations": [{"quote": "damage is not covered"}]})
        self.assertEqual(len(v), 2)
        self.assertEqual(guard.scan_output(out), [])
        self.assertIn("not covered", out["citations"][0]["quote"])


class Pii(unittest.TestCase):
    def test_deny_list_never_survives(self):
        for c in policies.load_cases().values():
            red = pii.redact(c["fnol"])
            self.assertEqual(pii.leaks(red, c["pii"]), [], c["id"])


class Agent(unittest.TestCase):
    def _run(self, cid, *msgs):
        return loop.run_agent(cid, FakeMcp(), chat=scripted(*msgs))

    def test_full_path_trace_id_and_citation_version(self):
        c = policies.load_cases()["C-S1"]
        run = self._run(
            "C-S1", act("get_claim", claim_id="C-S1"),
            act("check_coverage_in_force", policy_number=c["policy_number"], loss_date="2024-08-11"),
            act("lookup_policy", policy_number=c["policy_number"]),
            act("search_policy_wording", query="flood engine", product="motor", loss_date="2025-06-01"),
            act("refer_to_adjuster", claim_id="C-S1", reason="flood wording to review"),
            {"final": {"claim_id": "C-S1", "policy_number": c["policy_number"], "loss_date": "2024-08-11", "in_force": True,
                       "cited": [{"clause_id": "Exc-7.4", "endorsement_version": "2023"}], "deductible": 2500,
                       "sub_limit": {"peril": "flood", "amount": 50000}, "policy_limit": 400000,
                       "referral_reason": "This is covered.", "facts_for_adjuster": []}})
        self.assertEqual(run.termination, "completed")
        self.assertEqual(run.args_overridden[0]["enforced"], "2024-08-11")  # model sent the wrong date; code fixed it
        cit = run.delivered["citations"][0]
        self.assertEqual((cit["endorsement_version"], cit["loss_date_resolved_against"]), ("2023", "2024-08-11"))
        self.assertEqual(guard.scan_output(run.delivered), [])
        self.assertEqual(len(run.guard_violations), 1)
        spans = {s["name"] for s in run.trace_record["spans"]}
        self.assertTrue({"mcp.client.search_policy_wording", "mcp.server.search_policy_wording"} <= spans)
        self.assertTrue(all(run.trace_record["capstone"]["integrity"].values()))
        saved = json.loads(Path(REPORTS / "traces" / f"{run.trace.trace_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["trace_id"], run.trace.trace_id)
        self.assertEqual(pii.leaks(json.dumps(saved), policies.load_cases()["C-S1"]["pii"]), [])

    def test_unretrieved_citation_is_dropped(self):
        run = self._run("W6-005", act("get_claim", claim_id="W6-005"), act("refer_to_adjuster", claim_id="W6-005", reason="date unknown"),
                        {"final": {"claim_id": "W6-005", "cited": [{"clause_id": "Exc-9.9", "endorsement_version": "2025"}],
                                   "referral_reason": "loss date needs confirming", "facts_for_adjuster": []}})
        self.assertEqual(run.delivered["citations"], [])
        self.assertEqual(run.unretrieved_citations, [("Exc-9.9", "2025")])

    def test_missing_referral_is_refused(self):
        run = self._run("W6-005", act("get_claim", claim_id="W6-005"), {"final": {"claim_id": "W6-005"}},
                        {"final": {"claim_id": "W6-005"}}, {"final": {"claim_id": "W6-005"}})
        self.assertEqual(run.termination, "missing_steps")


class GuardCases(unittest.TestCase):
    """Permanent guards created by the closed failure->test loop (data/guard_cases.jsonl)."""

    def test_every_guard_case_red_before_green_after(self):
        from capstone.checks import loop_demo
        gcs = loop_demo.load_guard_cases()
        self.assertGreaterEqual(len(gcs), 5)
        for gc in gcs:
            self.assertFalse(loop_demo.check(gc, date_filter=False)[0], f"{gc['case_id']} should be red with the filter off")
            self.assertTrue(loop_demo.check(gc, date_filter=True)[0], f"{gc['case_id']} must be green with the filter on")


class Discovery(unittest.TestCase):
    def test_wording_tool_name_is_not_hardcoded_in_agent(self):
        for p in (CAPSTONE / "agent").glob("*.py"):
            self.assertNotIn("search_policy_wording", p.read_text(encoding="utf-8"), p.name)


if __name__ == "__main__":
    unittest.main()
