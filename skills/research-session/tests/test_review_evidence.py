"""Pack fidelity and evidence-state regressions; fixtures only, no model calls."""

import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "review_evidence.py"
DISPATCH = ROOT / "scripts" / "audit_dispatch.py"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("review_evidence_fixture", HELPER)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)
        self.source = self.directory / "test_source.py"
        self.source.write_bytes(b'records = {"checkpoint": [{"path": "a"}]}\r\nassert records\r\n')
        self.pack = self.directory / "pack.md"
        self.files = [self.source]

    def binding(self):
        return {"files": [{"path": str(p), "sha256": digest(p)} for p in self.files]}

    def lint(self, text, **metadata):
        self.pack.write_bytes(text.encode("utf-8"))
        return self.module.lint_pack(self.pack.read_bytes(), dict(self.binding(), **metadata))

    def test_source_render_preserves_crlf_and_exact_bytes(self):
        text = self.module.render_source(self.source)
        self.assertIn(self.source.read_bytes(), text.encode("utf-8"))
        self.assertEqual(self.lint(text)["source_blocks"], 1)

    def test_double_braces_cannot_masquerade_as_original(self):
        text = self.module.render_source(self.source).replace('records = {', 'records = {{')
        with self.assertRaises(ValueError):
            self.lint(text)

    def test_excerpt_is_bound_to_source_and_line_range(self):
        text = self.module.render_source(self.source, 2, 2)
        self.assertNotIn('"checkpoint"', text)
        self.assertEqual(self.lint(text)["source_blocks"], 1)
        with self.assertRaises(ValueError):
            self.lint(text.replace("assert records", "assert altered"))

    def test_fences_inside_source_and_missing_final_newline_roundtrip(self):
        self.source.write_bytes(b'# ```python\nx = 1')
        self.assertEqual(self.lint(self.module.render_source(self.source))["source_blocks"], 1)

    def test_unmarked_code_is_not_implicitly_original(self):
        with self.assertRaises(ValueError):
            self.lint('Full original source:\n```python\nx = 1\n```\n')
        data = self.lint('<!-- research-example -->\n```python\nx = 1\n```\n')
        self.assertEqual(data["example_blocks"], 1)

    def test_indented_unbound_fences_are_rejected_and_examples_remain_valid(self):
        for indent in (" ", "  ", "   "):
            for fence in ("```", "~~~"):
                with self.subTest(indent=indent, fence=fence):
                    block = "%s%spython\nforged = True\n%s%s\n" % (indent, fence, indent, fence)
                    with self.assertRaises(ValueError):
                        self.lint("Full original source:\n" + block)
                    self.assertEqual(self.lint("<!-- research-example -->\n" + block)["example_blocks"], 1)

    def test_source_not_in_manifest_is_rejected(self):
        self.files = []
        with self.assertRaises(ValueError):
            self.lint(self.module.render_source(self.source))

    def record(self, exit_code=0, summary="184 passed in 0.01s"):
        record = self.directory / "verification.json"
        command = [sys.executable, "-c", "import sys; print(%r); sys.exit(%d)" % (summary, exit_code)]
        self.module.run_verification(record, command, [self.source], self.directory)
        self.files.extend([record, record.with_suffix(".log")])
        return record

    def test_machine_verification_is_bound_to_inputs_log_and_command(self):
        record = self.record()
        data = self.lint('Full suite: 184 passed\n', verifications=[str(record)])
        self.assertEqual(data["verifications"][0]["exit_code"], 0)
        self.assertEqual(data["verifications"][0]["pytest_summary"], {"passed": 184})

    def test_positive_summary_cannot_hide_failed_log(self):
        record = self.record(1, "1 failed, 183 passed in 0.01s")
        with self.assertRaises(ValueError):
            self.lint('Full suite: 184 passed\n', verifications=[str(record)])
        data = self.lint('Full suite: 1 failed, 183 passed\n', verifications=[str(record)])
        self.assertEqual(data["verifications"][0]["exit_code"], 1)

    def test_two_contradictory_results_in_same_summary_are_incomplete(self):
        record = self.record()
        with self.assertRaises(ValueError):
            self.lint('Full suite: 184 passed (1 failed, 183 passed in 6.46s).',
                      verifications=[str(record)])

    def test_numerical_pytest_claim_requires_verification_not_a_path_only(self):
        with self.assertRaises(ValueError):
            self.lint('Full suite: 184 passed; see acceptance_cases.log')

    def test_changed_source_invalidates_old_verification_even_after_rebinding(self):
        record = self.record()
        self.source.write_bytes(b'changed = True\n')
        with self.assertRaises(ValueError):
            self.lint('Full suite: 184 passed', verifications=[str(record)])

    def test_same_content_with_later_mtime_requires_fresh_verification(self):
        record = self.record()
        future = datetime.datetime.now().timestamp() + 60
        os.utime(self.source, (future, future))
        with self.assertRaises(ValueError):
            self.lint('Full suite: 184 passed', verifications=[str(record)])

    def test_failed_test_summary_cannot_have_zero_exit(self):
        record = self.record(0, "1 failed, 183 passed in 0.01s")
        with self.assertRaises(ValueError):
            self.lint('Full suite: 1 failed, 183 passed', verifications=[str(record)])

    def test_evidence_status_is_explicit_and_does_not_invent_missing_history(self):
        data = {"timeline": [{"id": "T1", "event": "first designer access", "timestamp": None,
                              "verification_status": "absent", "evidence": "Never collected."}]}
        self.module.validate_metadata(data)
        data["timeline"][0]["verification_status"] = "verified_by_guess"
        with self.assertRaises(ValueError):
            self.module.validate_metadata(data)

    def test_dispute_requires_bound_reproduction_and_is_never_executed(self):
        value = {"disputes": [{"id": "D1", "finding_id": "B2", "claim": "Count is 142, not 140",
            "reproduction": {"artifact": str(self.source), "command": ["never-run-this-command"],
                             "observed": 142, "expected": 142}}]}
        self.module.validate_metadata(value, self.binding()["files"])
        del value["disputes"][0]["reproduction"]["command"]
        with self.assertRaises(ValueError):
            self.module.validate_metadata(value, self.binding()["files"])

    def test_lint_cli_is_local_and_cannot_grant_approval(self):
        self.pack.write_bytes(self.module.render_source(self.source).encode("utf-8"))
        manifest = self.directory / "inputs.json"
        manifest.write_text(json.dumps(dict(self.binding(), review_id="fixture")), encoding="utf-8")
        run = subprocess.run([sys.executable, "-B", str(DISPATCH), "--pack", str(self.pack),
                              "--inputs", str(manifest), "--lint-pack", "--port", "1"],
                             capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        value = json.loads(run.stdout)
        self.assertTrue(value["pack_lint"]["valid"])
        self.assertFalse(value["gate_passed"])
        self.assertTrue(value["local_only"])

    def test_source_cli_binary_output_matches_rendered_bytes_without_overwrite(self):
        output = self.directory / "source-block.md"
        command = [sys.executable, "-B", str(HELPER), "--source", str(self.source), "--output", str(output)]
        first = subprocess.run(command, capture_output=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(output.read_bytes(), self.module.render_source(self.source).encode("utf-8"))
        second = subprocess.run(command, capture_output=True, timeout=10)
        self.assertEqual(second.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
