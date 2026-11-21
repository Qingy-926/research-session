"""Cross-directory review ledger unit tests; no external model calls."""

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import review_ledger as ledger


def entry(kind, root, review_id="fixture-review", point=1, **extra):
    value = {
        "schema": ledger.SCHEMA, "entry_id": kind + "-" + str(len(str(extra))) + "-" + str(extra.get("round", "")),
        "kind": kind, "project_id": ledger.project_identity(root), "project_root": str(root),
        "created_at": "2026-11-16T00:00:00+00:00",
        "review_id": review_id, "point": point,
    }
    value.update(extra)
    if kind == "coverage" and value.get("complete") is True:
        value["scan_version"] = 2
    return value


class ProjectIdentityTest(unittest.TestCase):
    def test_number_is_stable_across_copy_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "科研"
            first = root / "project-991-original"
            copy = root / "project-991-copy"
            for path in (first, copy):
                (path / "01-资料" / "reviews").mkdir(parents=True)
            self.assertEqual(
                ledger.project_identity(ledger.project_root_for_path(first / "01-资料" / "reviews" / "a.md")),
                ledger.project_identity(ledger.project_root_for_path(copy / "01-资料" / "reviews" / "b.md")),
            )

    def test_same_number_in_another_category_is_distinct(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            first = root / "科研" / "project-991"
            second = root / "学习" / "project-991"
            for path in (first, second):
                (path / "01-资料" / "reviews").mkdir(parents=True)
            self.assertNotEqual(
                ledger.project_identity(ledger.project_root_for_path(first / "01-资料" / "reviews" / "a.md")),
                ledger.project_identity(ledger.project_root_for_path(second / "01-资料" / "reviews" / "b.md")),
            )

    def test_marker_identity_wins(self):
        with tempfile.TemporaryDirectory() as td:
            project = pathlib.Path(td) / "科研" / "project-991"
            (project / "01-资料" / "reviews").mkdir(parents=True)
            (project / ".review-project.json").write_text(
                json.dumps({"project_id": "REAL-PROJECT"}), encoding="utf-8")
            self.assertEqual(
                ledger.project_identity(ledger.project_root_for_path(project / "01-资料" / "reviews" / "a.md")),
                "real-project",
            )

    def test_nested_backup_uses_outer_project(self):
        with tempfile.TemporaryDirectory() as td:
            outer = pathlib.Path(td) / "project-991-original"
            nested = outer / "02-执行" / "备份" / "project-991-copy"
            (nested / "01-资料" / "reviews").mkdir(parents=True)
            self.assertEqual(
                ledger.norm_path(ledger.project_root_for_path(nested / "01-资料" / "reviews" / "a.md")),
                ledger.norm_path(outer),
            )

    def test_scope_rejects_backup_and_requires_project_input(self):
        with tempfile.TemporaryDirectory() as td:
            project = pathlib.Path(td) / "科研" / "project-991"
            canonical = project / "01-资料" / "reviews"
            backup = project / "02-执行" / "备份" / "batch" / "01-资料" / "reviews"
            canonical.mkdir(parents=True)
            backup.mkdir(parents=True)
            source = project / "protocol.md"
            source.write_text("fixture", encoding="utf-8")
            outside = pathlib.Path(td) / "outside.md"
            outside.write_text("fixture", encoding="utf-8")
            inputs = {"review_id": "fixture", "files": [{"path": str(source), "sha256": "0" * 64}]}
            ledger.validate_scope(canonical / "pack.md", inputs)
            with self.assertRaises(ValueError):
                ledger.validate_scope(backup / "pack.md", inputs)
            with self.assertRaises(ValueError):
                ledger.validate_scope(canonical / "pack.md", {"files": [{"path": str(outside), "sha256": "0" * 64}]})


class LedgerHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = pathlib.Path(self.temp.name) / "科研" / "project-991"
        self.canonical = self.project / "01-资料" / "reviews"
        self.canonical.mkdir(parents=True)
        self.pack_sha = "a" * 64

    def receipt(self, path, round_number=1, decision="待整改"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"binding": {"review_id": "fixture-review", "point": 1,
                                               "pack_sha256": self.pack_sha, "model": "fixture-review-model"},
                                    "round": round_number, "cycle": 1,
                                    "report": {"decision": decision}}), encoding="utf-8")
        return entry("receipt", self.project, receipt=str(path), receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     pack_sha256=self.pack_sha, model="fixture-review-model", round=round_number,
                     cycle=1, decision=decision)

    def test_missing_receipt_blocks(self):
        missing = self.canonical / "moved.review.json"
        rows = [self.receipt(missing)]
        missing.unlink()
        summary = ledger.summarize(rows, ledger.project_identity(self.project), "fixture-review", 1,
                                   self.canonical)
        blocked = ledger.blocking_reason(summary)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], "review_history")
        self.assertIn("不在原路径", blocked[1])

    def test_duplicate_copy_is_not_blocking(self):
        canonical = self.receipt(self.canonical / "canonical.review.json")
        copy = self.receipt(self.project / "02-执行" / "备份" / "copy.review.json")
        coverage = entry("coverage", self.project, complete=True, review_complete=True)
        summary = ledger.summarize([coverage, canonical, copy], ledger.project_identity(self.project),
                                   "fixture-review", 1, self.canonical)
        self.assertEqual(len(summary["duplicate_copies"]), 1)
        self.assertEqual(summary["outside_receipts"], [])
        self.assertIsNone(ledger.blocking_reason(summary))

    def test_outside_receipt_blocks(self):
        outside = self.receipt(self.project / "02-执行" / "backup.review.json", round_number=1)
        canonical = self.receipt(self.canonical / "canonical.review.json", round_number=2)
        summary = ledger.summarize([canonical, outside], ledger.project_identity(self.project),
                                   "fixture-review", 1, self.canonical)
        self.assertEqual(len(summary["outside_receipts"]), 1)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_history")

    def test_unresolved_intent_blocks_until_resolution(self):
        coverage = entry("coverage", self.project, entry_id="coverage-1", complete=True,
                         review_complete=True, failed_paths=[], skipped_paths=[], unreadable_paths=[])
        intent = entry("send_intent", self.project, entry_id="intent-1", pack_sha256=self.pack_sha)
        summary = ledger.summarize([coverage, intent], ledger.project_identity(self.project),
                                   "fixture-review", 1, self.canonical)
        self.assertEqual(len(summary["unresolved_intents"]), 1)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_history")
        resolution = entry("resolution", self.project, entry_id="resolution-1", resolves="intent-1",
                           note="fixture confirms no request was sent")
        summary = ledger.summarize([coverage, intent, resolution], ledger.project_identity(self.project),
                                   "fixture-review", 1, self.canonical)
        self.assertEqual(summary["unresolved_intents"], [])
        self.assertIsNone(ledger.blocking_reason(summary))

    def test_unknown_lock_blocks_and_orphan_lock_with_receipt_does_not(self):
        pack = str(self.canonical / "original.md")
        lock = entry("lock", self.project, entry_id="lock-1", lock=str(self.project / "old.lock"),
                     guard_id="exact-send", pack=pack, pack_sha256=self.pack_sha)
        summary = ledger.summarize([lock], ledger.project_identity(self.project), "fixture-review", 1,
                                   self.canonical)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_history")
        canonical = self.receipt(self.canonical / "canonical.review.json")
        canonical.update(guard_id="exact-send", pack=pack)
        coverage = entry("coverage", self.project, complete=True, review_complete=True)
        summary = ledger.summarize([coverage, lock, canonical], ledger.project_identity(self.project),
                                   "fixture-review", 1, self.canonical)
        self.assertEqual(summary["unknown_locks"], [])
        self.assertIsNone(ledger.blocking_reason(summary))

    def test_old_receipt_and_cached_classification_do_not_hide_a_new_guard(self):
        receipt = self.receipt(self.canonical / "old.review.json")
        receipt.update(guard_id="old-send", pack=str(self.canonical / "original.md"))
        lock = entry("lock", self.project, guard_id="new-send", pack=receipt["pack"],
                     pack_sha256=self.pack_sha, classification="orphan_lock_with_receipt")
        coverage = entry("coverage", self.project, complete=True, review_complete=True)
        state = ledger.summarize([coverage, lock, receipt], ledger.project_identity(self.project),
                                 "fixture-review", 1, self.canonical)
        self.assertEqual(state["unknown_locks"], [lock])
        self.assertEqual(ledger.blocking_reason(state)[0], "review_history")

    def test_incomplete_coverage_is_not_no_history(self):
        coverage = entry("coverage", self.project, entry_id="coverage-1", complete=False,
                         failed_paths=[{"path": str(self.project / "blocked"), "error": "denied"}])
        summary = ledger.summarize([coverage], ledger.project_identity(self.project), "fixture-review", 1,
                                   self.canonical)
        blocked = ledger.blocking_reason(summary)
        self.assertEqual(blocked[0], "review_scope")
        self.assertIn("扫描不完整", blocked[1])

    def test_review_scan_failure_marks_coverage_incomplete(self):
        def denied(root, onerror=None):
            if onerror is not None:
                onerror(PermissionError(13, "denied", str(self.canonical)))
            return iter(())

        with mock.patch.object(ledger.os, "walk", side_effect=denied):
            _, coverage = ledger.scan_project(str(self.project), ledger.project_identity(self.project),
                                              str(self.canonical))
        self.assertFalse(coverage["complete"])
        self.assertFalse(coverage["review_complete"])
        self.assertEqual(len(coverage["failed_paths"]), 1)
        self.assertEqual(coverage["skipped_paths"], [])

    def test_pruned_directory_cannot_be_claimed_as_complete(self):
        hidden = self.project / ".pytest_cache" / "archive"
        hidden.mkdir(parents=True)
        (hidden / "old.review.json").write_text("{}", encoding="utf-8")
        _, coverage = ledger.scan_project(str(self.project), ledger.project_identity(self.project),
                                          str(self.canonical))
        self.assertFalse(coverage["complete"])
        self.assertFalse(coverage["review_complete"])

    def test_non_review_scan_failure_is_recorded_and_blocked(self):
        blocked = self.project / "04-验证" / "artifact"

        def denied(root, onerror=None):
            if onerror is not None:
                onerror(PermissionError(13, "denied", str(blocked)))
            return iter(())

        with mock.patch.object(ledger.os, "walk", side_effect=denied):
            _, coverage = ledger.scan_project(str(self.project), ledger.project_identity(self.project),
                                              str(self.canonical))
        self.assertFalse(coverage["complete"])
        self.assertFalse(coverage["review_complete"])
        self.assertEqual(coverage["failed_paths"], [])
        self.assertEqual(len(coverage["skipped_paths"]), 1)
        summary = ledger.summarize([coverage], ledger.project_identity(self.project), "fixture-review", 1,
                                   self.canonical)
        self.assertFalse(summary["coverage_complete"])
        self.assertFalse(summary["coverage_review_complete"])
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_scope")

    def test_long_path_prefix_helpers(self):
        if os.name != "nt":
            self.skipTest("Windows long-path prefix only")
        self.assertTrue(ledger._long_path(r"C:\x").startswith("\\\\?\\"))
        self.assertEqual(ledger._strip_long_prefix("\\\\?\\C:\\x"), r"C:\x")
        self.assertEqual(ledger._strip_long_prefix("\\\\?\\UNC\\server\\share"), r"\\server\share")

    def test_link_exclusions_do_not_block_complete_coverage(self):
        scope = {
            "project_id": ledger.project_identity(self.project), "project_root": str(self.project),
            "canonical": ledger.canonical_reviews(self.project),
        }
        coverage = ledger.coverage_entry(scope, [], [], [],
                                         [{"path": "link", "target": "target", "error": "internal_link"}])
        self.assertTrue(coverage["complete"])
        self.assertTrue(coverage["review_complete"])
        self.assertEqual(len(coverage["link_exclusions"]), 1)
        summary = ledger.summarize([coverage], ledger.project_identity(self.project),
                                   "fixture-review", 1, self.canonical)
        self.assertIsNone(ledger.blocking_reason(summary))

    def test_external_review_link_failure_blocks_coverage(self):
        scope = {
            "project_id": ledger.project_identity(self.project), "project_root": str(self.project),
            "canonical": ledger.canonical_reviews(self.project),
        }
        coverage = ledger.coverage_entry(
            scope, [{"path": "link", "error": "external_link_to_reviews: X"}], [], [], [])
        self.assertFalse(coverage["complete"])
        self.assertFalse(coverage["review_complete"])

    def test_unreadable_entry_blocks_all_review_ids(self):
        unreadable = {
            "schema": ledger.SCHEMA, "entry_id": "unreadable-1", "kind": "unreadable",
            "project_id": ledger.project_identity(self.project), "project_root": str(self.project),
            "created_at": "2026-11-17T00:00:00+00:00",
            "path": str(self.project / "corrupt.review.json"), "error": "ValueError: bad json",
        }
        summary = ledger.summarize([unreadable], ledger.project_identity(self.project),
                                   "brand-new-review", 1, self.canonical)
        self.assertEqual(len(summary["unreadable"]), 1)
        blocked = ledger.blocking_reason(summary)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], "ledger")
        self.assertIn("无法解析", blocked[1])

    def test_two_copies_cannot_claim_same_review_object(self):
        copy_project = pathlib.Path(self.temp.name) / "科研" / "project-991-copy"
        (copy_project / "01-资料" / "reviews").mkdir(parents=True)
        first_scope = {
            "project_id": ledger.project_identity(self.project), "project_root": str(self.project),
            "canonical": ledger.canonical_reviews(self.project),
        }
        copy_scope = {
            "project_id": ledger.project_identity(copy_project), "project_root": str(copy_project),
            "canonical": ledger.canonical_reviews(copy_project),
        }
        self.assertEqual(first_scope["project_id"], copy_scope["project_id"])
        home = pathlib.Path(self.temp.name) / "ledger"
        ledger.append_entries(str(home), [ledger.coverage_entry(first_scope, [], [], [])])
        binding = {"review_id": "fixture-review", "point": 1,
                   "pack_sha256": "a" * 64, "model": "fixture-review-model"}
        first = ledger.claim_send(str(home), first_scope, binding, str(self.project / "pack-a.md"), 1)
        second = ledger.claim_send(str(home), copy_scope, binding, str(copy_project / "pack-b.md"), 1)
        self.assertTrue(first["claimed"])
        self.assertFalse(second["claimed"])
        self.assertEqual(second["reason"][0], "review_history")

    def test_claim_blocks_existing_receipt_unless_remediation_is_explicit(self):
        scope = {
            "project_id": ledger.project_identity(self.project), "project_root": str(self.project),
            "canonical": ledger.canonical_reviews(self.project),
        }
        home = pathlib.Path(self.temp.name) / "ledger"
        receipt = self.receipt(self.canonical / "canonical.review.json")
        coverage = entry("coverage", self.project, entry_id="coverage-1", complete=True,
                         review_complete=True, failed_paths=[], skipped_paths=[], unreadable_paths=[])
        ledger.append_entries(str(home), [coverage, receipt])
        binding = {"review_id": "fixture-review", "point": 1,
                   "pack_sha256": self.pack_sha, "model": "fixture-review-model"}
        blocked = ledger.claim_send(str(home), scope, binding, str(self.project / "first-pack.md"), 1)
        self.assertFalse(blocked["claimed"])
        self.assertEqual(blocked["reason"][0], "review_history")
        allowed = ledger.claim_send(str(home), scope, binding, str(self.project / "repair-pack.md"), 1,
                                    allow_existing_receipt=True, previous_path=receipt["receipt"])
        self.assertTrue(allowed["claimed"])


    def test_old_skipped_coverage_cannot_authorize_send(self):
        coverage = entry("coverage", self.project, complete=False, review_complete=True,
                         failed_paths=[], skipped_paths=[{"path": "unknown", "error": "denied"}], unreadable_paths=[])
        summary = ledger.summarize([coverage], ledger.project_identity(self.project), "new-review", 1, self.canonical)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_scope")

    def test_legacy_complete_coverage_requires_rescan(self):
        coverage = entry("coverage", self.project, complete=True, review_complete=True)
        del coverage["scan_version"]
        summary = ledger.summarize([coverage], ledger.project_identity(self.project), "new", 1, self.canonical)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_scope")

    def test_fixed_unreadable_file_is_reconciled_without_erasing_history(self):
        scope = {"project_id": ledger.project_identity(self.project), "project_root": str(self.project),
                 "canonical": ledger.canonical_reviews(self.project)}
        home = str(pathlib.Path(self.temp.name) / "ledger")
        bad = self.canonical / "x.attempt-1.json"
        bad.write_text("{bad", encoding="utf-8")
        ledger.scan_and_register(str(self.project), scope["project_id"], scope["canonical"], home)
        self.assertEqual(ledger.blocking_reason(ledger.preflight(home, scope, "new", 1))[0], "ledger")
        bad.write_text(json.dumps({"binding": {"review_id": "old", "point": 1}}), encoding="utf-8")
        ledger.scan_and_register(str(self.project), scope["project_id"], scope["canonical"], home)
        self.assertIsNone(ledger.blocking_reason(ledger.preflight(home, scope, "new", 1)))
        rows = ledger.read_entries(home)
        self.assertTrue(any(row["kind"] == "unreadable" for row in rows))
        self.assertTrue(any(row["kind"] == "attempt" for row in rows))

    def test_remediation_claim_rechecks_completed_second_round(self):
        scope = {"project_id": ledger.project_identity(self.project), "project_root": str(self.project),
                 "canonical": ledger.canonical_reviews(self.project)}
        home = str(pathlib.Path(self.temp.name) / "ledger")
        coverage = ledger.coverage_entry(scope, [], [], [])
        first = self.receipt(self.canonical / "first.review.json", 1)
        second = self.receipt(self.canonical / "second.review.json", 2)
        ledger.append_entries(home, [coverage, first, second])
        binding = {"review_id": "fixture-review", "point": 1, "pack_sha256": "c" * 64}
        claim = ledger.claim_send(home, scope, binding, str(self.project / "repair.md"), 1,
                                  allow_existing_receipt=True, previous_path=first["receipt"])
        self.assertFalse(claim["claimed"])
        self.assertEqual(claim["reason"][0], "review_limit")


    def test_unreadable_records_for_distinct_paths_are_not_deduplicated(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        for name in ("a.review.json", "b.review.json"):
            (self.canonical / name).write_text("{bad", encoding="utf-8")
        ledger.scan_and_register(str(self.project), ledger.project_identity(self.project), str(self.canonical), home)
        rows = [r for r in ledger.read_entries(home) if r["kind"] == "unreadable"]
        self.assertEqual(len(rows), 2)

    def test_recovered_coverage_after_failure_is_latest(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        pid = ledger.project_identity(self.project)
        ledger.scan_and_register(str(self.project), pid, str(self.canonical), home)
        bad = self.canonical / "bad.review.json"
        bad.write_text("{bad", encoding="utf-8")
        ledger.scan_and_register(str(self.project), pid, str(self.canonical), home)
        bad.write_text(json.dumps({"binding": {"review_id": "other", "point": 1}}), encoding="utf-8")
        ledger.scan_and_register(str(self.project), pid, str(self.canonical), home)
        scope = {"project_id": pid, "project_root": str(self.project), "canonical": str(self.canonical)}
        self.assertTrue(ledger.preflight(home, scope, "fresh", 1)["coverage_complete"])

    def test_missing_corrupt_file_is_not_auto_resolved(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        pid = ledger.project_identity(self.project)
        scope = {"project_id": pid, "project_root": str(self.project), "canonical": str(self.canonical)}
        bad = self.canonical / "bad.review.json"
        bad.write_text("{bad", encoding="utf-8")
        ledger.scan_and_register(str(self.project), pid, str(self.canonical), home)
        bad.unlink()
        ledger.scan_and_register(str(self.project), pid, str(self.canonical), home)
        self.assertEqual(ledger.blocking_reason(ledger.preflight(home, scope, "new", 1))[0], "ledger")

    def test_incomplete_coverage_blocks_even_with_a_receipt(self):
        row = self.receipt(self.canonical / "first.review.json")
        coverage = entry("coverage", self.project, complete=False, review_complete=True,
                         skipped_paths=[{"path": "unknown", "error": "denied"}])
        summary = ledger.summarize([row, coverage], ledger.project_identity(self.project), "fixture-review", 1, self.canonical)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_scope")


    def test_unfinished_other_receipt_blocks_remediation(self):
        scope = {"project_id": ledger.project_identity(self.project), "project_root": str(self.project), "canonical": str(self.canonical)}
        home = str(pathlib.Path(self.temp.name) / "ledger")
        first = self.receipt(self.canonical / "first.review.json", 1)
        other = self.receipt(self.canonical / "unfinished.review.json", 0, "unknown")
        ledger.append_entries(home, [ledger.coverage_entry(scope, [], [], []), first, other])
        result = ledger.claim_send(home, scope, {"review_id": "fixture-review", "point": 1, "pack_sha256": "c"*64},
                                   str(self.project / "new.md"), 1, allow_existing_receipt=True,
                                   previous_path=first["receipt"])
        self.assertFalse(result["claimed"])

    def test_legacy_coverage_tracks_omitted_unreadable_path(self):
        bad_a = str(self.canonical / "a.review.json")
        bad_b = str(self.canonical / "b.review.json")
        old = entry("coverage", self.project, complete=False, review_complete=False, unreadable_paths=[
             {"path": bad_a, "error": "bad"}, {"path": bad_b, "error": "bad"}])
        home = str(pathlib.Path(self.temp.name) / "ledger")
        ledger.append_entries(home, [old])
        scope = {"project_id": ledger.project_identity(self.project), "project_root": str(self.project), "canonical": str(self.canonical)}
        ledger.scan_and_register(str(self.project), scope["project_id"], scope["canonical"], home)
        self.assertEqual(ledger.blocking_reason(ledger.preflight(home, scope, "new", 1))[0], "ledger")


    def test_incomplete_other_registered_root_blocks_send(self):
        other = pathlib.Path(self.temp.name) / "research" / "project-991-copy"
        other.mkdir(parents=True)
        bad = entry("coverage", self.project, complete=False, review_complete=False)
        bad["project_root"] = str(other)
        good = entry("coverage", self.project, complete=True, review_complete=True)
        summary = ledger.summarize([bad, good], ledger.project_identity(self.project), "new", 1, self.canonical)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_scope")

    def test_old_same_hash_receipt_cannot_resolve_new_intent(self):
        scope = {"project_id": ledger.project_identity(self.project), "project_root": str(self.project), "canonical": str(self.canonical)}
        cov = ledger.coverage_entry(scope, [], [], [])
        intent = entry("send_intent", self.project, entry_id="intent", intent_id="new-intent", pack_sha256=self.pack_sha)
        receipt = self.receipt(self.canonical / "old.review.json")
        receipt["created_at"] = "2026-11-18T00:00:00+00:00"
        summary = ledger.summarize([cov, intent, receipt], scope["project_id"], "fixture-review", 1, self.canonical)
        self.assertEqual(ledger.blocking_reason(summary)[0], "review_history")

    def test_legacy_intent_can_be_resolved_with_verified_receipt(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        scope = {"project_id": ledger.project_identity(self.project), "project_root": str(self.project),
                 "canonical": str(self.canonical)}
        intent = entry("send_intent", self.project, intent_id="old-intent", pack_sha256=self.pack_sha)
        receipt = self.receipt(self.canonical / "old.review.json")
        path = pathlib.Path(receipt["receipt"])
        pack = self.canonical / "old.md"
        pack.write_bytes(b"review package and appended report")
        self.pack_sha = hashlib.sha256(b"review package").hexdigest()
        intent["pack_sha256"] = self.pack_sha
        receipt["pack_sha256"] = self.pack_sha
        path.write_text(json.dumps({"schema": "research-session/review/v1",
                                    "binding": {"review_id": "fixture-review", "point": 1, "pack_sha256": self.pack_sha},
                                    "round": 1, "report": {"decision": "待整改"},
                                    "pack": {"path": str(pack), "before_bytes": len(b"review package"),
                                             "after_sha256": hashlib.sha256(pack.read_bytes()).hexdigest()}}), encoding="utf-8")
        receipt["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        added = ledger.append_entries(home, [ledger.coverage_entry(scope, [], [], []), intent, receipt])
        ledger.resolve_intent(home, added[1]["entry_id"], "verified historical receipt",
                              receipt_path=receipt["receipt"])
        summary = ledger.summarize(ledger.read_entries(home), scope["project_id"], "fixture-review", 1, self.canonical)
        self.assertEqual(summary["unresolved_intents"], [])

    def test_legacy_resolution_rejects_unmatched_receipt(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        intent = entry("send_intent", self.project, intent_id="old-intent", pack_sha256="d" * 64)
        receipt = self.receipt(self.canonical / "old.review.json")
        added = ledger.append_entries(home, [intent, receipt])
        with self.assertRaises(ValueError):
            ledger.resolve_intent(home, added[0]["entry_id"], "unmatched", receipt_path=receipt["receipt"])

    def test_legacy_resolution_rejects_receipt_bound_to_another_intent(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        intent = entry("send_intent", self.project, intent_id="intent-b", pack_sha256=self.pack_sha)
        receipt = self.receipt(self.canonical / "old.review.json")
        path = pathlib.Path(receipt["receipt"])
        path.write_text(json.dumps({"schema": "research-session/review/v1", "intent_id": "intent-a",
                                    "binding": {"review_id": "fixture-review", "point": 1, "pack_sha256": self.pack_sha},
                                    "round": 1, "report": {"decision": "待整改"},
                                    "pack": {"path": str(self.canonical / "old.md"), "before_bytes": 1,
                                             "after_sha256": "e" * 64}}), encoding="utf-8")
        receipt["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        added = ledger.append_entries(home, [intent, receipt])
        with self.assertRaises(ValueError):
            ledger.resolve_intent(home, added[0]["entry_id"], "conflict", receipt_path=str(path))

    def test_legacy_resolution_rejects_changed_bound_pack(self):
        home = str(pathlib.Path(self.temp.name) / "ledger")
        intent = entry("send_intent", self.project, intent_id="old", pack_sha256=self.pack_sha)
        receipt = self.receipt(self.canonical / "old.review.json")
        pack = self.canonical / "old.md"
        pack.write_bytes(b"changed")
        path = pathlib.Path(receipt["receipt"])
        path.write_text(json.dumps({"schema": "research-session/review/v1",
                                    "binding": {"review_id": "fixture-review", "point": 1, "pack_sha256": self.pack_sha},
                                    "round": 1, "report": {"decision": "待整改"},
                                    "pack": {"path": str(pack), "before_bytes": 2,
                                             "after_sha256": "e" * 64}}), encoding="utf-8")
        receipt["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        added = ledger.append_entries(home, [intent, receipt])
        with self.assertRaises(ValueError):
            ledger.resolve_intent(home, added[0]["entry_id"], "changed", receipt_path=str(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
