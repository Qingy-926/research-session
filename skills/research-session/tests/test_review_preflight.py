"""Local contract validation and recovery, without an external model."""

import copy
import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from test_review_closure import report


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dispatch.py"
SPEC = importlib.util.spec_from_file_location("dispatch_preflight", SCRIPT)
DISPATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DISPATCH)


class PreflightTest(unittest.TestCase):
    def test_structured_evidence_preserves_counterexample_without_rejection(self):
        value = report("待整改", blocker=True)
        value["findings"][0]["evidence"] = {
            "source_category": "静态推演，未运行",
            "actual_evidence": "A list-valued field enumerates zero records.",
            "synthetic_counterexample": {"path": [], "sha256": []},
        }
        try:
            parsed = DISPATCH.parse_review(json.dumps(value), 7)
        except ValueError as exc:
            self.fail("Structured evidence was incorrectly rejected: %s" % exc)
        self.assertEqual(parsed["decision"], "待整改")
        self.assertEqual(json.loads(parsed["findings"][0]["evidence"])["synthetic_counterexample"],
                         {"path": [], "sha256": []})

    def test_extra_historical_closure_is_not_an_active_blocker_or_approval(self):
        previous = {"report": report("待整改", blocker=True)}
        value = report("待整改", blocker=True)
        value["closed_findings"] = [
            {"id": "B1", "status": "未解决", "evidence": "The listed counterexample still fails."},
            {"id": "B02", "status": "已解决", "evidence": "Historical source field was supplied."},
        ]
        try:
            parsed = DISPATCH.parse_review(json.dumps(value), 7, previous)
        except ValueError as exc:
            self.fail("Historical annotation discarded the substantive decision: %s" % exc)
        self.assertEqual([r["id"] for r in parsed["closed_findings"]], ["B1"])
        self.assertEqual(parsed["historical_closed_findings"][0]["id"], "B02")
        self.assertEqual(parsed["decision"], "待整改")

    def test_unknown_closure_cannot_replace_the_required_active_id(self):
        previous = {"report": report("待整改", blocker=True)}
        value = report()
        value["closed_findings"] = [{"id": "B01", "status": "已解决", "evidence": "Wrong identifier."}]
        with self.assertRaises(ValueError):
            DISPATCH.parse_review(json.dumps(value), 7, previous)

    def test_empty_structured_evidence_is_still_rejected(self):
        for evidence in ({}, {"source_category": "suggestion"}, {"actual_evidence": " "}):
            with self.subTest(evidence=evidence):
                value = report("待整改", blocker=True)
                value["findings"][0]["evidence"] = evidence
                with self.assertRaises(ValueError):
                    DISPATCH.parse_review(json.dumps(value), 7)

    def test_each_mandatory_finding_field_remains_required(self):
        for field in ("id", "kind", "location", "evidence", "impact", "change", "acceptance"):
            with self.subTest(field=field):
                value = report("待整改", blocker=True)
                del value["findings"][0][field]
                with self.assertRaises(ValueError):
                    DISPATCH.parse_review(json.dumps(value), 7)

    def test_markdown_json_fence_is_a_lossless_local_repair(self):
        text = "```json\n" + json.dumps(report("待整改", blocker=True)) + "\n```"
        try:
            parsed = DISPATCH.parse_review(text, 7)
        except ValueError as exc:
            self.fail("JSON fence should not require a new model call: %s" % exc)
        self.assertEqual(parsed["decision"], "待整改")

    def test_local_cli_validation_succeeds_without_pack_or_proxy(self):
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "report.json"
            path.write_text(json.dumps(report()), encoding="utf-8")
            run = subprocess.run([sys.executable, "-B", str(SCRIPT), "--point", "1",
                                  "--validate-report", str(path), "--port", "1"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            data = json.loads(run.stdout)
            self.assertTrue(data["schema_validated"])
            self.assertFalse(data["gate_passed"])
            self.assertFalse(data["review_completed"])

    def test_response_template_roundtrips_through_the_same_validator(self):
        try:
            contract = DISPATCH.review_contract(7, {"report": report("待整改", blocker=True)})
        except AttributeError as exc:
            self.fail("Missing producer/consumer contract: %s" % exc)
        template = contract["response_template"]
        template["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": "fixture.json::pass=true"}]
        parsed = DISPATCH.parse_review(json.dumps(template), 7, {"report": report("待整改", blocker=True)})
        self.assertEqual(parsed["decision"], "通过")
        self.assertEqual(contract["active_blocker_ids"], ["B1"])

    def test_missing_legacy_verification_status_is_unverified_not_retroactive_proof(self):
        previous = {"report": report("待整改", blocker=True)}
        value = report()
        value["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": "Historical claim, not rechecked."}]
        parsed = DISPATCH.parse_review(json.dumps(value), 7, previous)
        self.assertEqual(parsed["closed_findings"][0]["verification_status"], "unverified")

    def test_quantitative_claim_requires_reproducible_locator(self):
        value = report("待整改", blocker=True)
        value["findings"][0]["quantitative_claim"] = {
            "artifact": "inputs.json", "observed": 140, "expected": 142}
        with self.assertRaises(ValueError):
            DISPATCH.parse_review(json.dumps(value), 7)
        value["findings"][0]["quantitative_claim"]["json_pointer"] = "/entry_count"
        self.assertEqual(DISPATCH.parse_review(json.dumps(value), 7)["decision"], "待整改")

    def test_disputes_must_be_answered_in_same_incremental_round(self):
        previous = {"report": report("待整改", blocker=True)}
        reproduction = {"artifact": "entries.json", "json_pointer": "/entries", "observed": 142, "expected": 142}
        disputes = [{"id": "D1", "finding_id": "B1", "claim": "The count was wrong.", "reproduction": reproduction}]
        value = report("待整改", blocker=True)
        value["closed_findings"] = [{"id": "B1", "status": "未解决", "evidence": "Other missing evidence remains."}]
        with self.assertRaises(ValueError):
            DISPATCH.parse_review(json.dumps(value), 7, previous, disputes)
        value["dispute_resolutions"] = [{"id": "D1", "status": "accepted", "evidence": "142 records, 141 unique paths.",
                                         "verification_status": "verified_external", "reproduction": reproduction}]
        parsed = DISPATCH.parse_review(json.dumps(value), 7, previous, disputes)
        self.assertEqual(parsed["decision"], "待整改")
        self.assertEqual(parsed["dispute_resolutions"][0]["status"], "accepted")

    def test_unresolved_dispute_does_not_silently_pass(self):
        reproduction = {"artifact": "entries.json", "command": ["count", "entries.json"], "observed": 142, "expected": 142}
        disputes = [{"id": "D1", "finding_id": "B1", "claim": "Count disputed.", "reproduction": reproduction}]
        value = report()
        value["dispute_resolutions"] = [{"id": "D1", "status": "unresolved", "evidence": "Not checked.",
                                         "verification_status": "unverified", "reproduction": reproduction}]
        with self.assertRaises(ValueError):
            DISPATCH.parse_review(json.dumps(value), 7, disputes=disputes)

    def test_implementation_blocker_requires_patch_or_specific_repair_limit(self):
        value = report("待整改", blocker=True)
        value["findings"][0]["domain"] = "implementation"
        with self.assertRaises(ValueError):
            DISPATCH.parse_review(json.dumps(value), 7)
        value["findings"][0]["repair"] = {
            "status": "needs_context", "reason": "Need the bound comparator function and its registry schema, not the entire pack."}
        self.assertEqual(DISPATCH.parse_review(json.dumps(value), 7)["decision"], "待整改")

    def test_reviewer_patch_is_bound_and_not_itself_an_approval(self):
        value = report("待整改", blocker=True)
        value["findings"][0].update(domain="implementation", repair={
            "status": "patch_proposed", "reason": "Reject list-valued roles before comparing differences.",
            "patch": "--- a/comparator.py\n+++ b/comparator.py\n@@ -1 +1 @@\n-pass\n+raise ValueError()\n",
            "test_case": "Call comparator with identical role lists; assert CONTROL_INVALID.",
            "targets": [{"path": "comparator.py", "sha256": "a" * 64}],
        })
        parsed = DISPATCH.parse_review(json.dumps(value), 7)
        result, code = DISPATCH.review_result({"report": parsed, "round": 1, "binding": {}}, "fixture.review.json")
        self.assertEqual(code, 3)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["proposed_repair_ids"], ["B1"])
        self.assertIn("apply_bounded_reviewer_patch", result["next_action"])
        del value["findings"][0]["repair"]["targets"]
        with self.assertRaises(ValueError):
            DISPATCH.parse_review(json.dumps(value), 7)

    def test_optional_null_repair_on_nonimplementation_finding_does_not_crash(self):
        value = report("待整改", blocker=True)
        value["findings"][0]["repair"] = None
        parsed = DISPATCH.parse_review(json.dumps(value), 7)
        result, code = DISPATCH.review_result({"report": parsed, "round": 1, "binding": {}}, "fixture.review.json")
        self.assertEqual(code, 3)
        self.assertEqual(result["proposed_repair_ids"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
