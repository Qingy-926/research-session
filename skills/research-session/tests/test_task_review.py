"""Task-bound continued remediation, using the real dispatcher and local provider."""

import datetime as dt
import concurrent.futures
import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_review_closure as closure


class TaskReviewTest(unittest.TestCase):
    _shared_server = None
    _server_thread = None
    server = closure.ReviewClosureTest.server
    setUpClass = classmethod(closure.ReviewClosureTest.setUpClass.__func__)
    tearDownClass = classmethod(closure.ReviewClosureTest.tearDownClass.__func__)
    setUp = closure.ReviewClosureTest.setUp
    update_manifest = closure.ReviewClosureTest.update_manifest
    register_fixture_as_historical = closure.ReviewClosureTest.register_fixture_as_historical
    pack = closure.ReviewClosureTest.pack
    invoke = closure.ReviewClosureTest.invoke
    _restore_ledger_env = staticmethod(closure.ReviewClosureTest._restore_ledger_env)
    _restore_model_env = staticmethod(closure.ReviewClosureTest._restore_model_env)

    def approval(self, mode="autonomous", **overrides):
        now = dt.datetime.now(dt.timezone.utc)
        source = self.project / "task-approval.md"
        source.write_text("Approved: repair and independently review this fixture task.", encoding="utf-8")
        path = self.project / "task-approval.json"
        data = {
            "schema": "research-session/review-task/v1", "task_id": "fixture-task",
            "mode": mode, "project_id": self.project.parent.name.lower() + "/project-991",
            "review_ids": [self.review_id], "points": [1],
            "approval_source": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
            "started_at": (now - dt.timedelta(minutes=1)).isoformat(),
            "deadline": (now + dt.timedelta(hours=1)).isoformat(),
            "closeout_seconds": 60, "max_requests": 5, "reserved_requests": 0, "revoked": False,
        }
        data.update(overrides)
        path.write_text(json.dumps(data), encoding="utf-8")
        self.approval_files = [path, source]
        return path

    def progress(self, index):
        self.source.write_text("threshold = %d\n" % index, encoding="utf-8")
        self.check_serial = getattr(self, "check_serial", 0) + 1
        verification = self.project / ("check-%d-%d.json" % (index, self.check_serial))
        run = subprocess.run([
            sys.executable, "-B", str(closure.ROOT / "scripts/review_evidence.py"),
            "--verify", str(verification), "--input", str(self.source), "--cwd", str(self.project),
            "--", sys.executable, "-c",
            "from pathlib import Path; assert Path(%r).read_text().strip() == %r"
            % (str(self.source), "threshold = %d" % index),
        ], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.update_manifest(extra=[*self.approval_files, verification, result["log"]])
        data = json.loads(self.manifest.read_text())
        data["verifications"] = [str(verification)]
        data["remediation_progress"] = [
            {"finding_id": "B1", "path": str(self.source), "verification": str(verification)}
        ]
        self.manifest.write_text(json.dumps(data), encoding="utf-8")

    def adverse(self, previous=False):
        value = closure.report("待整改", blocker=True)
        if previous:
            value["closed_findings"] = [{"id": "B1", "status": "未解决", "evidence": "Verified repair still needs judgment."}]
        closure.Proxy.response = value

    def test_task_continues_same_id_past_two_rounds_in_both_modes(self):
        for mode in ("autonomous", "unattended"):
            with self.subTest(mode=mode):
                # Independent projects/ledgers for the two modes.
                if mode == "unattended":
                    self.doCleanups()
                    self.setUp()
                approval = self.approval(mode)
                self.update_manifest(extra=self.approval_files)
                options = ("--task-approval", str(approval), "--timeout", "5")
                self.adverse()
                run, first = self.invoke(self.pack(), extra=options)
                self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
                self.assertEqual(first["disposition"], "task_revise")
                self.progress(1)
                self.adverse(previous=True)
                run, second = self.invoke(self.pack("second.md"), first["receipt"], extra=options)
                self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
                self.assertEqual(second["round"], 2)
                self.progress(2)
                closure.Proxy.response = closure.report()
                closure.Proxy.response["closed_findings"] = [
                    {"id": "B1", "status": "已解决", "evidence": "The current threshold and independent checks are explicit."}
                ]
                run, third = self.invoke(self.pack("third.md"), second["receipt"], extra=options)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(third["round"], 3)
                self.assertEqual(third["binding"]["review_id"], self.review_id)
                self.assertEqual(third["cycle"], 1)
                self.assertEqual(len(closure.Proxy.requests), 3)

    def test_task_rejects_no_progress_and_expired_or_wrong_project_approval(self):
        approval = self.approval()
        self.update_manifest(extra=self.approval_files)
        options = ("--task-approval", str(approval), "--timeout", "5")
        self.adverse()
        _, first = self.invoke(self.pack(), extra=options)
        before = len(closure.Proxy.requests)
        run, rejected = self.invoke(self.pack("no-progress.md", "Request again with no actual repairs."), first["receipt"], extra=options)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("progress", rejected["error"])
        self.assertEqual(len(closure.Proxy.requests), before)
        self.progress(1)
        for key, value in (("revoked", True), ("project_id", "other/project"),
                           ("deadline", "2020-01-01T00:00:00+00:00"),
                           ("mode", "controlled"), ("max_requests", 1)):
            original = json.loads(approval.read_text())
            changed = dict(original, **{key: value})
            approval.write_text(json.dumps(changed), encoding="utf-8")
            self.update_manifest(extra=self.approval_files)
            run, _ = self.invoke(self.pack(key + ".md"), first["receipt"], extra=options)
            self.assertNotEqual(run.returncode, 0, key)
            self.assertEqual(len(closure.Proxy.requests), before, key)
            approval.write_text(json.dumps(original), encoding="utf-8")

    def test_renewed_task_continues_to_round_four_and_keeps_cross_point_budget(self):
        approval = self.approval(mode="unattended", max_requests=4, points=[1, 2])
        self.update_manifest(extra=self.approval_files)
        options = ("--task-approval", str(approval), "--timeout", "5")
        self.adverse()
        run, current = self.invoke(self.pack(), extra=options)
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        for index in (1, 2):
            self.progress(index)
            self.adverse(previous=True)
            run, current = self.invoke(self.pack(f"prior-{index}.md"), current["receipt"], extra=options)
            self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertEqual(current["round"], 3)
        original_bytes = approval.read_bytes()
        original_receipt = self.directory / "prior-2.md.review.json"
        receipt_bytes = original_receipt.read_bytes()
        old = json.loads(original_bytes)
        now = dt.datetime.now(dt.timezone.utc)
        decision = self.project / "renewal.md"
        decision.write_text("Approved: continue the same fixture with its cumulative budget.", encoding="utf-8")
        renewed = self.project / "renewal.json"
        new = dict(old, deadline=(now + dt.timedelta(hours=2)).isoformat(),
                   approval_source={"path": str(decision), "sha256": hashlib.sha256(decision.read_bytes()).hexdigest()},
                   continuation={
                       "previous": {"path": str(approval), "sha256": hashlib.sha256(original_bytes).hexdigest()},
                       "effective_at": now.isoformat(),
                   })
        renewed.write_text(json.dumps(new), encoding="utf-8")
        self.approval_files.extend([renewed, decision])
        self.progress(3)
        options = ("--task-approval", str(renewed), "--timeout", "5")
        for key, change in (
            ("started_at", now.isoformat()),
            ("max_requests", 5),
            ("points", [1, 2, 3]),
            ("revoked", True),
            ("continuation", dict(new["continuation"], effective_at=(now + dt.timedelta(hours=1)).isoformat())),
            ("continuation", dict(new["continuation"], previous={"path": str(approval), "sha256": "0" * 64})),
        ):
            renewed.write_text(json.dumps(dict(new, **{key: change})), encoding="utf-8")
            self.progress(3)
            run, _ = self.invoke(self.pack(f"reject-{self.check_serial}.md"), str(original_receipt), extra=options)
            self.assertEqual(run.returncode, 2, key)
            self.assertEqual(len(closure.Proxy.requests), 3, key)
        renewed.write_text(json.dumps(new), encoding="utf-8")
        self.progress(3)
        self.adverse(previous=True)
        run, fourth = self.invoke(self.pack("renewed.md"), str(original_receipt), extra=options)
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertEqual((fourth["round"], fourth["cycle"]), (4, 1))
        self.assertEqual(fourth["binding"]["review_id"], self.review_id)
        self.assertEqual(original_receipt.read_bytes(), receipt_bytes)
        self.assertEqual(approval.read_bytes(), original_bytes)
        run, stopped = self.invoke(self.pack("result-point.md"), extra=(*options, "--point", "2"))
        self.assertEqual(run.returncode, 2)
        self.assertIn("task request budget exhausted", stopped["error"])
        self.assertEqual(len(closure.Proxy.requests), 4)

    def test_renewal_after_expiry_checks_receipts_in_their_own_windows(self):
        sys.path.insert(0, str(closure.ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(closure.ROOT / "scripts"))
        import review_task

        start = dt.datetime(2026, 11, 22, tzinfo=dt.timezone.utc)
        old_path = self.approval(started_at=start.isoformat(),
                                 deadline=(start + dt.timedelta(hours=2)).isoformat())
        old = json.loads(old_path.read_bytes())
        new_path = self.project / "new.json"
        source = self.project / "new.md"
        source.write_text("Approve this continuation after expiry.", encoding="utf-8")

        def pin(path):
            return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

        effective = start + dt.timedelta(hours=4)
        new_path.write_text(json.dumps(dict(
            old, approval_source=pin(source), deadline=(effective + dt.timedelta(hours=1)).isoformat(),
            continuation={"previous": pin(old_path), "effective_at": effective.isoformat()},
        )), encoding="utf-8")
        binding = {"review_id": self.review_id, "point": 1,
                   "files": [pin(path) for path in [*self.approval_files, new_path, source]]}
        scope = {"project_id": old["project_id"]}

        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return effective + dt.timedelta(minutes=1)

        with patch.object(review_task.dt, "datetime", Clock):
            with self.assertRaisesRegex(ValueError, "budget"):
                review_task.validate(str(old_path), binding, scope, 5)
            binding["task_approval"] = review_task.validate(str(new_path), binding, scope, 5)
            prior = dict(binding=dict(task_approval=dict(
                pin(old_path), task_id=old["task_id"], mode=old["mode"])))
            chain = review_task.approval_chain(str(new_path), binding["files"])
            review_task.validate_receipt(dict(prior, created_at=(start + dt.timedelta(hours=1)).isoformat()), chain)
            with self.assertRaisesRegex(ValueError, "window"):
                review_task.validate_receipt(dict(prior, created_at=(start + dt.timedelta(hours=3)).isoformat()), chain)

    def test_task_legacy_two_round_stop_continues_without_new_cycle(self):
        self.adverse()
        _, first = self.invoke(self.pack())
        self.source.write_text("threshold = 1\n", encoding="utf-8")
        self.update_manifest()
        self.adverse(previous=True)
        _, second = self.invoke(self.pack("legacy-second.md"), first["receipt"])
        self.assertEqual(second["disposition"], "needs_user_decision")
        approval = self.approval()
        self.progress(2)
        run, third = self.invoke(self.pack("continued.md"), second["receipt"],
                                 extra=("--task-approval", str(approval), "--timeout", "5"))
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertEqual(third["round"], 3)
        self.assertEqual(third["disposition"], "task_revise")
        self.assertEqual(third["cycle"], 1)

    def test_task_rejected_first_can_be_repaired_but_not_repeated(self):
        approval = self.approval()
        self.update_manifest(extra=self.approval_files)
        options = ("--task-approval", str(approval), "--timeout", "5")
        self.adverse()
        closure.Proxy.response["decision"] = "不通过"
        _, first = self.invoke(self.pack(), extra=options)
        self.progress(1)
        self.adverse(previous=True)
        run, second = self.invoke(self.pack("fixed.md"), first["receipt"], extra=options)
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertEqual(second["round"], 2)
        self.progress(2)
        _, third = self.invoke(self.pack("fixed2.md"), second["receipt"], extra=options)
        self.progress(1)
        before = len(closure.Proxy.requests)
        run, stopped = self.invoke(self.pack("oscillation.md"), third["receipt"], extra=options)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("repeats", stopped["error"])
        self.assertEqual(len(closure.Proxy.requests), before)

    def test_task_request_allocation_is_atomic_across_review_objects(self):
        approval = self.approval(max_requests=1, review_ids=[self.review_id, "second-object"])
        self.update_manifest(extra=self.approval_files)
        other_manifest = self.directory / "other-inputs.json"
        other = json.loads(self.manifest.read_text())
        other["review_id"] = "second-object"
        other_manifest.write_text(json.dumps(other), encoding="utf-8")
        packs = [self.pack("object-a.md"), self.pack("object-b.md")]

        def send(pair):
            pack, manifest = pair
            return subprocess.run([
                sys.executable, "-B", str(closure.SCRIPT), "--pack", str(pack), "--point", "1",
                "--inputs", str(manifest), "--port", str(self.server.server_port), "--no-stream",
                "--task-approval", str(approval), "--timeout", "5",
            ], capture_output=True, text=True, encoding="utf-8", timeout=15)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            runs = list(executor.map(send, zip(packs, [self.manifest, other_manifest])))
        self.assertEqual(sum(run.returncode == 0 for run in runs), 1, [r.stdout + r.stderr for r in runs])
        self.assertEqual(len(closure.Proxy.requests), 1)

    def test_task_budget_uses_the_approved_request_count(self):
        # 6 and 12 are concrete approval values, never a plugin default or cap.
        for limit in (6, 12):
            if limit == 12:
                self.doCleanups()
                self.setUp()
            approval = self.approval(max_requests=limit, reserved_requests=0)
            self.update_manifest(extra=self.approval_files)
            options = ("--task-approval", str(approval), "--timeout", "5")
            self.adverse()
            run, current = self.invoke(self.pack(), extra=options)
            self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
            for index in range(1, limit):
                self.progress(index)
                self.adverse(previous=True)
                run, nxt = self.invoke(self.pack("budget-%d-%d.md" % (limit, index)),
                                       current["receipt"], extra=options)
                self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
                self.assertEqual(nxt["round"], index + 1)
                current = nxt
            self.assertEqual(limit, len(closure.Proxy.requests))
            self.progress(limit)
            self.adverse(previous=True)
            run, stopped = self.invoke(self.pack("budget-%d-over.md" % limit),
                                       current["receipt"], extra=options)
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertIn("task request budget exhausted", stopped["error"])
            self.assertEqual(limit, len(closure.Proxy.requests))

    def test_task_unknown_send_never_retries_on_another_package(self):
        approval = self.approval()
        self.update_manifest(extra=self.approval_files)
        options = ("--task-approval", str(approval), "--timeout", "5")
        closure.Proxy.response_status = "incomplete"
        run, first = self.invoke(self.pack(), extra=options)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        run, _ = self.invoke(self.pack("another.md", "Different envelope."), extra=options)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(len(closure.Proxy.requests), 1)

    def test_task_counts_legacy_explicit_retry_as_two_requests(self):
        approval = self.approval(max_requests=2)
        self.update_manifest(extra=self.approval_files)
        self.adverse()
        closure.Proxy.incomplete_remaining = 1
        run, first = self.invoke(self.pack(), extra=("--retry-truncated-tokens", "65536"))
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertEqual(len(closure.Proxy.requests), 2)
        self.progress(1)
        self.adverse(previous=True)
        run, stopped = self.invoke(self.pack("third-request.md"), first["receipt"],
                                   extra=("--task-approval", str(approval), "--timeout", "5"))
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("budget exhausted", stopped["error"])
        self.assertEqual(len(closure.Proxy.requests), 2)

    def test_task_format_recovered_original_survives_three_continuations(self):
        self.adverse()
        value = closure.Proxy.response
        value["findings"][0].update(domain="implementation", repair={
            "status": "patch_proposed", "reason": "Sufficient source.",
            "patch": "fixture patch", "test_case": {"input": "fixture", "expected": "reject"},
            "targets": [{"path": "protocol.md", "sha256": "a" * 64}],
        })
        _, first = self.invoke(self.pack())
        path = self.directory / "first.md.review.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        original.update(round=0, review_counted=False, report=None,
                        error="裁决合同未通过: repair补丁缺少patch/test_case")
        original["binding"]["rules_sha256"] = "8ae413ca0983351c65206f03c1c7b46e3b4db5b873132ca54ac14886016f62ac"
        path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        self.register_fixture_as_historical()
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        approval = self.approval(max_requests=6)
        options = ("--task-approval", str(approval), "--timeout", "5")
        previous = first["receipt"]
        for index in (1, 2, 3):
            self.progress(index)
            self.adverse(previous=True)
            recovery = ("--recover-previous-format",) if index == 1 else ()
            run, current = self.invoke(self.pack("recovered-%d.md" % index), previous, extra=options + recovery)
            self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
            self.assertEqual(current["round"], index + 1)
            previous = current["receipt"]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original_hash)
