"""Behavior regressions for bounded research reviews; no external model calls."""

from __future__ import annotations

import hashlib
import http.server
import contextlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from typing import Any, Callable, ClassVar, cast
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_dispatch.py"


def report(decision="通过", blocker=False) -> dict[str, Any]:
    findings = []
    if blocker:
        findings.append({
            "id": "B1", "kind": "blocking", "location": "protocol.md:2",
            "evidence": "代码仿真: fixture.json::threshold = null",
            "impact": "The current gate is undefined.",
            "change": "Set the preregistered threshold before results exist.",
            "acceptance": "The threshold is explicit and its fixture passes.",
        })
    return {
        "decision": decision, "summary": "Review of the supplied evidence only.",
        "checklist": [{"item": i, "status": "通过", "reason": ""} for i in range(1, 8)],
        "findings": findings, "closed_findings": [],
    }


class Proxy(http.server.BaseHTTPRequestHandler):
    requests = []
    response: Any = report()
    callbacks: ClassVar[dict[str, Callable[[], Any]]] = {}
    response_status = "completed"
    incomplete_remaining = 0

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(payload)
        callback = type(self).callbacks.get("on_request")
        if callback is not None:
            callback()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if type(self).incomplete_remaining > 0:
            type(self).incomplete_remaining -= 1
            self.wfile.write(json.dumps({"id": "resp_cut", "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"}}).encode("utf-8"))
            return
        text = self.response if isinstance(self.response, str) else json.dumps(self.response, ensure_ascii=False)
        self.wfile.write(json.dumps({"status": self.response_status, "output_text": text}).encode("utf-8"))


class ReviewClosureTest(unittest.TestCase):
    _shared_server: ClassVar[http.server.HTTPServer | None] = None
    _server_thread: ClassVar[threading.Thread | None] = None

    def __init__(self, methodName="runTest"):
        super().__init__(methodName)
        self.directory = pathlib.Path()
        self.source = pathlib.Path()
        self.manifest = pathlib.Path()

    @property
    def server(self) -> http.server.HTTPServer:
        server = type(self)._shared_server
        if server is None:
            raise RuntimeError("Review test server has not started")
        return server

    @classmethod
    def setUpClass(cls):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        cls._shared_server = server
        cls._server_thread = thread
        thread.start()

    @classmethod
    def tearDownClass(cls):
        server, thread = cls._shared_server, cls._server_thread
        if server is None or thread is None:
            raise RuntimeError("Review test server was not started")
        server.shutdown()
        server.server_close()
        thread.join()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = pathlib.Path(temp.name) / "project-991-fixture"
        self.directory = self.project / "01-资料" / "reviews"
        self.directory.mkdir(parents=True)
        self.review_id = "fixture-protocol-point-1"
        self.source = self.directory / "protocol.md"
        self.source.write_text("threshold = null\n", encoding="utf-8")
        self.manifest = self.directory / "inputs.json"
        self.update_manifest()
        old_ledger = os.environ.get("RESEARCH_SESSION_LEDGER_DIR")
        os.environ["RESEARCH_SESSION_LEDGER_DIR"] = str(pathlib.Path(temp.name) / "ledger")
        self.addCleanup(self._restore_ledger_env, old_ledger)
        old_model = os.environ.get("RESEARCH_SESSION_MODEL")
        os.environ["RESEARCH_SESSION_MODEL"] = "fixture-review-model"
        self.addCleanup(self._restore_model_env, old_model)
        Proxy.requests.clear()
        Proxy.response = report()
        Proxy.callbacks.clear()
        Proxy.response_status = "completed"
        Proxy.incomplete_remaining = 0

    @staticmethod
    def _restore_ledger_env(old: str | None) -> None:
        if old is None:
            os.environ.pop("RESEARCH_SESSION_LEDGER_DIR", None)
        else:
            os.environ["RESEARCH_SESSION_LEDGER_DIR"] = old

    @staticmethod
    def _restore_model_env(old: str | None) -> None:
        if old is None:
            os.environ.pop("RESEARCH_SESSION_MODEL", None)
        else:
            os.environ["RESEARCH_SESSION_MODEL"] = old

    def update_manifest(self, extra=(), review_id=None):
        files = [self.source, *[pathlib.Path(p) for p in extra]]
        self.manifest.write_text(json.dumps({
            "review_id": review_id or self.review_id,
            "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
        }), encoding="utf-8")

    def register_fixture_as_historical(self):
        # Tests construct historical receipts by editing a synthetic receipt. Register
        # that final fixture into a fresh ledger, just as a first backfill would.
        os.environ["RESEARCH_SESSION_LEDGER_DIR"] = str(self.project.parent / "historical-ledger")
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), "--ledger-backfill", str(self.project)],
                             capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def pack(self, name="first.md", text="Current protocol and evidence: threshold is explicit."):
        path = self.directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def invoke(self, pack, previous=None, inputs=True, extra=()):
        command = [sys.executable, "-B", str(SCRIPT), "--pack", str(pack), "--point", "1",
                   "--port", str(self.server.server_port), "--no-stream"]
        if inputs:
            command.extend(["--inputs", str(self.manifest)])
        if previous:
            command.extend(["--previous", str(previous)])
        command.extend(extra)
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
        payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
        return result, payload

    def test_legacy_conditional_approval_never_returns_success(self):
        Proxy.response = "总体裁决：修改后放行"
        run, data = self.invoke(self.pack(), inputs=False)
        self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse(data.get("gate_passed", True))

    def test_pending_is_completed_but_not_passed(self):
        Proxy.response = report("待整改", blocker=True)
        pack = self.pack()
        original = pack.read_bytes()
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertTrue(data["review_completed"])
        self.assertFalse(data["gate_passed"])
        self.assertFalse(data["ok"])
        self.assertEqual(data["disposition"], "revise_once")
        self.assertTrue(pack.read_bytes().startswith(original))
        self.assertTrue(pathlib.Path(data["receipt"]).is_file())

    def test_concurrent_first_reviews_for_same_object_send_at_most_once(self):
        entered = threading.Event()
        two_requests = threading.Event()
        release = threading.Event()

        def hold_first():
            entered.set()
            if len(Proxy.requests) >= 2:
                two_requests.set()
            release.wait(5)

        Proxy.callbacks["on_request"] = hold_first
        first_pack = self.pack("parallel-a.md")
        second_pack = self.pack("parallel-b.md", "Different evidence for the same object.")

        def start(pack):
            return subprocess.Popen(
                [sys.executable, "-B", str(SCRIPT), "--pack", str(pack), "--point", "1",
                 "--port", str(self.server.server_port), "--no-stream", "--inputs", str(self.manifest)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

        first = start(first_pack)
        try:
            self.assertTrue(entered.wait(5), "first request never arrived")
            second = start(second_pack)
            try:
                two_requests.wait(2)
            finally:
                release.set()
                second_out, second_err = second.communicate(timeout=10)
        finally:
            release.set()
            first_out, first_err = first.communicate(timeout=10)
        self.assertEqual(len(Proxy.requests), 1, (first_out, first_err, second_out, second_err))

    def test_crash_after_request_blocks_another_pack_for_same_object(self):
        entered = threading.Event()
        release = threading.Event()
        Proxy.callbacks["on_request"] = lambda: (entered.set(), release.wait(8))
        first = subprocess.Popen(
            [sys.executable, "-B", str(SCRIPT), "--pack", str(self.pack("crash.md")),
             "--point", "1", "--port", str(self.server.server_port), "--no-stream",
             "--inputs", str(self.manifest)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(entered.wait(5))
            first.kill()
            first.communicate(timeout=5)
            run, data = self.invoke(self.pack("after-crash.md"))
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertFalse(data["gate_passed"])
            self.assertEqual(len(Proxy.requests), 1)
        finally:
            release.set()
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=5)

    def test_unknown_transport_result_is_not_automatically_retried(self):
        def disconnect():
            raise ConnectionResetError("fixture disconnect after request received")

        Proxy.callbacks["on_request"] = disconnect
        run, data = self.invoke(self.pack("disconnect.md"))
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_concurrent_remediation_for_same_baseline_sends_once(self):
        Proxy.response = report("待整改", blocker=True)
        first, baseline = self.invoke(self.pack("baseline.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        Proxy.requests.clear()
        entered = threading.Event()
        second_seen = threading.Event()
        release = threading.Event()

        def hold():
            entered.set()
            if len(Proxy.requests) >= 2:
                second_seen.set()
            release.wait(5)

        Proxy.callbacks["on_request"] = hold
        commands = []
        for name in ("repair-a.md", "repair-b.md"):
            commands.append(subprocess.Popen(
                [sys.executable, "-B", str(SCRIPT), "--pack", str(self.pack(name, name)),
                 "--point", "1", "--port", str(self.server.server_port), "--no-stream",
                 "--inputs", str(self.manifest), "--previous", baseline["receipt"]],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"))
            if len(commands) == 1:
                self.assertTrue(entered.wait(5))
        try:
            second_seen.wait(2)
        finally:
            release.set()
            outputs = [process.communicate(timeout=10) for process in commands]
        self.assertEqual(len(Proxy.requests), 1, outputs)

    def test_cross_copy_guard_blocks_competitor_and_owner_finishes_once(self):
        Proxy.response = report("待整改", blocker=True)
        first, baseline = self.invoke(self.pack("baseline.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        copy_project = self.project.parent / "project-991-copy"
        copy_reviews = copy_project / "01-资料" / "reviews"
        copy_reviews.mkdir(parents=True)
        previous = copy_reviews / "baseline.md.review.json"
        previous.write_bytes(pathlib.Path(baseline["receipt"]).read_bytes())
        source = copy_project / "protocol.md"
        source.write_text("threshold = 0.1\n", encoding="utf-8")
        manifest = copy_project / "inputs.json"
        manifest.write_text(json.dumps({"review_id": self.review_id, "files": [
            {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        ]}), encoding="utf-8")
        pack = copy_reviews / "repair-b.md"
        pack.write_text("B1 fixed in the copied project.", encoding="utf-8")
        entered = self.project.parent / "entered"
        release = self.project.parent / "release"
        wrapper = self.project.parent / "pause_claim.py"
        wrapper.write_text(
            "import pathlib,sys,time\n"
            "sys.path.insert(0, sys.argv.pop(1))\n"
            "entered=pathlib.Path(sys.argv.pop(1)); release=pathlib.Path(sys.argv.pop(1))\n"
            "import audit_dispatch as dispatch\n"
            "original=dispatch.review_ledger.claim_send\n"
            "def paused(*args, **kwargs):\n"
            "    entered.write_text('ready')\n"
            "    limit=time.monotonic()+12\n"
            "    while not release.exists() and time.monotonic()<limit: time.sleep(.02)\n"
            "    if not release.exists(): raise RuntimeError('barrier timeout')\n"
            "    return original(*args, **kwargs)\n"
            "dispatch.review_ledger.claim_send=paused\n"
            "dispatch.main()\n",
            encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-B", str(wrapper), str(ROOT / "scripts"), str(entered), str(release),
             "--pack", str(pack), "--point", "1", "--port", str(self.server.server_port),
             "--no-stream", "--inputs", str(manifest), "--previous", str(previous)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            deadline = time.monotonic() + 10
            while not entered.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(entered.exists(), "second copy did not reach the claim barrier")
            self.source.write_text("threshold = 0.1\n", encoding="utf-8")
            self.update_manifest()
            Proxy.response = report()
            Proxy.response["closed_findings"] = [
                {"id": "B1", "status": "已解决", "evidence": "fixture passes"}]
            run, result = self.invoke(self.pack("repair-a.md", "B1 fixed."), baseline["receipt"])
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertEqual(result["stage"], "review_history")
        finally:
            release.write_text("go", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=15)
        output = json.loads(stdout)
        self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(output["round"], 2)
        self.assertEqual(len(Proxy.requests), 2)

    def test_concurrent_copies_with_same_identity_send_at_most_once(self):
        copy_project = self.project.parent / "project-991-copy"
        copy_reviews = copy_project / "01-资料" / "reviews"
        copy_reviews.mkdir(parents=True)
        copy_source = copy_project / "protocol.md"
        copy_source.write_text("Copied evidence for the same review object.", encoding="utf-8")
        copy_manifest = copy_project / "inputs.json"
        copy_manifest.write_text(json.dumps({
            "review_id": self.review_id,
            "files": [{"path": str(copy_source),
                       "sha256": hashlib.sha256(copy_source.read_bytes()).hexdigest()}],
        }), encoding="utf-8")
        first_pack = self.pack("parallel-a.md")
        second_pack = copy_reviews / "parallel-b.md"
        second_pack.write_text("Copied evidence package.", encoding="utf-8")
        entered = threading.Event()
        release = threading.Event()
        Proxy.callbacks["on_request"] = lambda: (entered.set(), release.wait(5))

        def start(pack, manifest):
            return subprocess.Popen(
                [sys.executable, "-B", str(SCRIPT), "--pack", str(pack), "--point", "1",
                 "--port", str(self.server.server_port), "--no-stream", "--inputs", str(manifest)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

        first = start(first_pack, self.manifest)
        second = start(second_pack, copy_manifest)
        try:
            self.assertTrue(entered.wait(5), "first request never arrived")
            time.sleep(0.5)
        finally:
            release.set()
            outputs = [process.communicate(timeout=10) for process in (first, second)]
        self.assertEqual(len(Proxy.requests), 1, outputs)
        self.assertEqual(sorted(process.returncode for process in (first, second)), [0, 2], outputs)

    def test_corrupt_historical_receipt_blocks_new_review_id(self):
        corrupt = self.project / "02-执行" / "备份" / "corrupt.review.json"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{not valid json", encoding="utf-8")
        self.update_manifest(review_id="new-review-after-corrupt")
        pack = self.pack("new-object.md", "A new object after a corrupt historical record.")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "ledger")
        self.assertEqual(len(Proxy.requests), 0)

    def test_moved_receipt_cannot_restart_review(self):
        Proxy.response = report("待整改", blocker=True)
        first, data = self.invoke(self.pack("first.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        receipt = pathlib.Path(data["receipt"])
        moved = self.project / "02-执行" / "old-location" / receipt.name
        moved.parent.mkdir(parents=True)
        receipt.rename(moved)
        second, result = self.invoke(self.pack("second.md", "Changed evidence"))
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertEqual(result["stage"], "review_history")
        self.assertEqual(len(Proxy.requests), 1)

    def test_copied_project_with_same_number_cannot_start_second_first_review(self):
        first, _ = self.invoke(self.pack("first.md"))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        copy_project = self.project.parent / "project-991-copy"
        copy_reviews = copy_project / "01-资料" / "reviews"
        copy_reviews.mkdir(parents=True)
        copy_source = copy_project / "protocol.md"
        copy_source.write_text("Copied research data.", encoding="utf-8")
        copy_manifest = copy_project / "inputs.json"
        copy_manifest.write_text(json.dumps({
            "review_id": self.review_id,
            "files": [{"path": str(copy_source),
                       "sha256": hashlib.sha256(copy_source.read_bytes()).hexdigest()}],
        }), encoding="utf-8")
        self.source = copy_source
        self.manifest = copy_manifest
        copy_pack = copy_reviews / "copy.md"
        copy_pack.write_text("Copied project review.", encoding="utf-8")
        run, result = self.invoke(copy_pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(result["stage"], "review_history")
        self.assertEqual(len(Proxy.requests), 1)

    def test_same_number_in_another_category_is_not_blocked(self):
        first, _ = self.invoke(self.pack("first.md"))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        other_project = self.project.parent / "其他" / "project-991"
        other_reviews = other_project / "01-资料" / "reviews"
        other_reviews.mkdir(parents=True)
        other_source = other_project / "protocol.md"
        other_source.write_text("Independent project data.", encoding="utf-8")
        other_manifest = other_project / "inputs.json"
        other_manifest.write_text(json.dumps({
            "review_id": self.review_id,
            "files": [{"path": str(other_source),
                       "sha256": hashlib.sha256(other_source.read_bytes()).hexdigest()}],
        }), encoding="utf-8")
        self.source = other_source
        self.manifest = other_manifest
        other_pack = other_reviews / "pack.md"
        other_pack.write_text("Independent project review.", encoding="utf-8")
        run, result = self.invoke(other_pack)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(result["gate_passed"])
        self.assertEqual(len(Proxy.requests), 2)

    def test_backup_pack_cannot_send(self):
        backup = self.project / "02-执行" / "备份" / "batch" / "01-资料" / "reviews"
        backup.mkdir(parents=True)
        pack = backup / "pack.md"
        pack.write_text("Backup package.", encoding="utf-8")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "review_scope")
        self.assertEqual(len(Proxy.requests), 0)

    def test_nonnumbered_project_with_root_marker_can_review(self):
        with tempfile.TemporaryDirectory() as td:
            project = pathlib.Path(td) / "research-project"
            reviews = project / "01-资料" / "reviews"
            reviews.mkdir(parents=True)
            project.joinpath("AGENTS.md").write_text("Project marker.", encoding="utf-8")
            source = project / "protocol.md"
            source.write_text("Research data.", encoding="utf-8")
            manifest = project / "inputs.json"
            manifest.write_text(json.dumps({
                "review_id": "marker-project-fixture",
                "files": [{"path": str(source),
                           "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
            }), encoding="utf-8")
            pack = reviews / "pack.md"
            pack.write_text("Review fixture.", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--pack", str(pack), "--point", "1",
                 "--port", str(self.server.server_port), "--no-stream", "--inputs", str(manifest),
                 "--ledger-dir", os.environ["RESEARCH_SESSION_LEDGER_DIR"]],
                capture_output=True, text=True, encoding="utf-8", timeout=15)
            data = json.loads(run.stdout)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertTrue(data["gate_passed"])
            self.assertEqual(len(Proxy.requests), 1)

    def test_unnumbered_backup_with_copied_project_marker_cannot_send(self):
        with tempfile.TemporaryDirectory() as td:
            project = pathlib.Path(td) / "research-project"
            (project / "01-资料" / "reviews").mkdir(parents=True)
            project.joinpath("AGENTS.md").write_text("Project marker.", encoding="utf-8")
            backup = project / "02-执行" / "备份" / "snapshot"
            copied_reviews = backup / "01-资料" / "reviews"
            copied_reviews.mkdir(parents=True)
            backup.joinpath("AGENTS.md").write_text("Copied marker.", encoding="utf-8")
            source = backup / "protocol.md"
            source.write_text("Copied data.", encoding="utf-8")
            manifest = backup / "inputs.json"
            manifest.write_text(json.dumps({
                "review_id": "marker-backup-fixture",
                "files": [{"path": str(source),
                           "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
            }), encoding="utf-8")
            pack = copied_reviews / "pack.md"
            pack.write_text("Copied review.", encoding="utf-8")
            run = subprocess.run(
                [sys.executable, "-B", str(SCRIPT), "--pack", str(pack), "--point", "1",
                 "--port", str(self.server.server_port), "--no-stream", "--inputs", str(manifest),
                 "--ledger-dir", os.environ["RESEARCH_SESSION_LEDGER_DIR"]],
                capture_output=True, text=True, encoding="utf-8", timeout=15)
            data = json.loads(run.stdout)
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertEqual(data["stage"], "review_scope")
            self.assertEqual(len(Proxy.requests), 0)

    def test_backfill_classifies_copies_and_orphan_lock_without_rewriting(self):
        Proxy.response = report("待整改", blocker=True)
        first, data = self.invoke(self.pack("first.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        receipt = pathlib.Path(data["receipt"])
        before = receipt.read_bytes()
        backup = self.project / "02-执行" / "备份" / "batch" / "01-资料" / "reviews"
        backup.mkdir(parents=True)
        copy = backup / receipt.name
        copy.write_bytes(before)
        lock = backup / ".review-send-fixture.lock"
        sent = json.loads(before)
        lock.write_text(json.dumps({"pid": 999999, "pack": str(self.directory / "first.md"),
                                    "guard_id": sent["guard_id"],
                                    "pack_sha256": sent["binding"]["pack_sha256"],
                                    "review_id": self.review_id, "point": 1}), encoding="utf-8")
        lock_before = lock.read_bytes()
        run = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--ledger-backfill", str(self.project),
             "--ledger-dir", os.environ["RESEARCH_SESSION_LEDGER_DIR"]],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(json.loads(run.stdout)["ok"])
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(lock.read_bytes(), lock_before)
        ledger_file = pathlib.Path(os.environ["RESEARCH_SESSION_LEDGER_DIR"]) / "ledger.jsonl"
        rows = [json.loads(line) for line in ledger_file.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        classifications = {row.get("classification") for row in rows if row["kind"] == "receipt"}
        self.assertIn("canonical_receipt", classifications)
        self.assertIn("duplicate_copy", classifications)
        self.assertTrue(any(row.get("classification") == "orphan_lock_with_receipt"
                            for row in rows if row["kind"] == "lock"))

    def test_receipt_published_before_cleanup_has_explicit_local_reconciliation(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import review_ledger as ledger
        Proxy.response = report("待整改", blocker=True)
        first, data = self.invoke(self.pack("first.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        receipt = pathlib.Path(data["receipt"])
        before = receipt.read_bytes()
        sent = json.loads(before)
        key = hashlib.sha256(json.dumps([self.review_id, 1], ensure_ascii=False).encode()).hexdigest()
        lock = self.directory / (".review-send-" + key + ".lock")
        lock.write_text(json.dumps({"pid": 4294967294, "pack": sent["pack"]["path"],
                                   "review_id": self.review_id, "point": 1,
                                   "guard_id": sent["guard_id"],
                                   "pack_sha256": sent["binding"]["pack_sha256"]}), encoding="utf-8")
        import datetime
        moment = datetime.datetime.fromisoformat(
            re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", sent["created_at"])).timestamp() - 1
        os.utime(lock, (moment, moment))
        home = os.environ["RESEARCH_SESSION_LEDGER_DIR"]
        scope = {"project_id": ledger.project_identity(self.project),
                 "project_root": str(self.project), "canonical": str(self.directory)}
        state = ledger.preflight(home, scope, self.review_id, 1)
        target = next(row for row in state["group"] if row["kind"] == "lock")
        proof = ledger.send_lock_recovery_template(home, target["entry_id"])
        review = self.project / "reconciliation-review.txt"
        review.write_text("Independently checked exact receipt and exited owner.", encoding="utf-8")
        proof.update(decision="reconcile_receipted_send", outcome="receipt_preserved",
                     operator="operator", reviewer="independent-reviewer", basis="Exact receipt binding",
                     limitations="Original verdict and review budget remain unchanged",
                     reviewed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     review_record={"path": str(review), "sha256": hashlib.sha256(review.read_bytes()).hexdigest()})
        evidence = self.project / "reconciliation.json"
        evidence.write_text(json.dumps(proof), encoding="utf-8")
        result = ledger.recover_send_lock(home, target["entry_id"], str(evidence),
                                          hashlib.sha256(evidence.read_bytes()).hexdigest())
        self.assertTrue(result["applied"])
        self.assertFalse(lock.exists())
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(len(Proxy.requests), 1)
        self.assertEqual(ledger.preflight(home, scope, self.review_id, 1)["unknown_locks"], [])

    def test_unresolved_intent_blocks_resend_and_can_be_resolved(self):
        Proxy.incomplete_remaining = 3
        first, _ = self.invoke(self.pack("first.md"))
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        for lock in self.directory.glob(".review-send-*.lock"):
            lock.unlink()
        Proxy.incomplete_remaining = 0
        second, result = self.invoke(self.pack("second.md", "Changed evidence"))
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertEqual(result["stage"], "review_history")
        self.assertEqual(len(Proxy.requests), 1)
        entry_id = result["ledger_entries"][0]["entry_id"]
        resolve = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--resolve-ledger", entry_id,
             "--ledger-note", "fixture confirms the upstream request never completed",
             "--ledger-dir", os.environ["RESEARCH_SESSION_LEDGER_DIR"]],
            capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(resolve.returncode, 0, resolve.stdout + resolve.stderr)
        third, final = self.invoke(self.pack("third.md", "Another full package"))
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertTrue(final["gate_passed"])
        self.assertEqual(len(Proxy.requests), 2)

    def test_previous_receipt_in_another_directory_is_rejected(self):
        Proxy.response = report("待整改", blocker=True)
        first, baseline = self.invoke(self.pack("baseline.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        other = self.directory / "another-directory"
        other.mkdir()
        pack = other / "revision.md"
        pack.write_text("Revised evidence.", encoding="utf-8")
        second, data = self.invoke(pack, baseline["receipt"])
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_invalid_remediation_cannot_resend_from_original_baseline(self):
        Proxy.response = report("待整改", blocker=True)
        first, baseline = self.invoke(self.pack("baseline.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        Proxy.response = "invalid structured response"
        failed, data = self.invoke(self.pack("invalid-repair.md", "Attempted repair."), baseline["receipt"])
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertIsNone(json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))["report"])
        again, result = self.invoke(self.pack("another-repair.md", "Another full package."), baseline["receipt"])
        self.assertEqual(again.returncode, 2, again.stdout + again.stderr)
        self.assertEqual(result["stage"], "binding")
        self.assertEqual(len(Proxy.requests), 2)

    def test_only_unambiguous_pass_returns_zero(self):
        run, data = self.invoke(self.pack())
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(data["gate_passed"])
        self.assertEqual(data["decision"], "通过")

    def test_fresh_official_template_placeholder_is_not_an_old_review(self):
        pack = self.pack(text="# Review\n\n## 5. 复核结论（Codex 填写）\n待填写。\n")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertTrue(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_actual_appended_review_without_receipt_is_not_resubmitted(self):
        pack = self.pack(text="# Review\n\n## 5. 复核结论（fixture-review-model 于 2026-11-07 自动填写）\n旧意见。\n")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(Proxy.requests, [])

    def test_pass_with_blocker_is_incomplete_not_approved(self):
        Proxy.response = report("通过", blocker=True)
        run, data = self.invoke(self.pack())
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertFalse(data["gate_passed"])

    def test_explicit_noncompleted_response_status_never_approves(self):
        for status in ("cancelled", "in_progress", "queued"):
            with self.subTest(status=status):
                self.update_manifest(review_id="status-" + status)
                Proxy.response_status = status
                run, data = self.invoke(self.pack(status + ".md", "Protocol evidence for " + status))
                self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                self.assertFalse(data["gate_passed"])
                self.assertEqual(data["upstream"]["response_status"], status)

    def test_failed_pack_write_does_not_publish_an_empty_receipt(self):
        import importlib.util

        pack = self.pack()
        original = pack.read_bytes()
        spec = importlib.util.spec_from_file_location("dispatch_write_fixture", SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load audit dispatch test fixture")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_open = open

        def deny_pack_write(path, mode="r", *args, **kwargs):
            if str(path) == str(pack) and mode == "r+b":
                raise PermissionError("fixture transient write denial")
            return real_open(path, mode, *args, **kwargs)

        argv = [str(SCRIPT), "--pack", str(pack), "--point", "1", "--inputs", str(self.manifest),
                "--port", str(self.server.server_port), "--no-stream"]
        captured = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch("builtins.open", side_effect=deny_pack_write), contextlib.redirect_stdout(captured):
            with self.assertRaises(SystemExit) as exited:
                module.main()
        self.assertEqual(exited.exception.code, 2, captured.getvalue())
        self.assertEqual(pack.read_bytes(), original)
        self.assertFalse(pathlib.Path(str(pack) + ".review.json").exists())
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "send_guard")
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_rejection_is_not_transport_failure(self):
        Proxy.response = report("不通过", blocker=True)
        run, data = self.invoke(self.pack())
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertTrue(data["review_completed"])
        self.assertEqual(data["decision"], "不通过")
        self.assertEqual(data["disposition"], "rejected_stop")

    def test_rejected_first_round_cannot_be_resent_as_normal_remediation(self):
        Proxy.response = report("不通过", blocker=True)
        first, data = self.invoke(self.pack("rejected.md"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        second, result = self.invoke(self.pack("rejected-remediation.md", "Changed evidence."),
                                     data["receipt"])
        self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_missing_binding_does_not_send_a_request(self):
        run, data = self.invoke(self.pack(), inputs=False)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(Proxy.requests, [])

    def test_unchanged_pack_reuses_receipt_without_another_request(self):
        pack = self.pack()
        first, first_data = self.invoke(pack)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        saved = pack.read_bytes()
        second, data = self.invoke(pack)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertTrue(data["reused"])
        self.assertEqual(data["receipt"], first_data["receipt"])
        self.assertEqual(pack.read_bytes(), saved)
        self.assertEqual(len(Proxy.requests), 1)

    def test_changed_dependency_invalidates_cached_approval(self):
        pack = self.pack()
        first, _ = self.invoke(pack)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.source.write_text("threshold = changed\n", encoding="utf-8")
        self.update_manifest()
        second, data = self.invoke(pack)
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_cached_approval_rechecks_verification_freshness_even_if_bytes_are_unchanged(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("evidence_cached_fixture", ROOT / "scripts" / "review_evidence.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load evidence test fixture")
        evidence = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evidence)
        record = self.directory / "verification.json"
        evidence.run_verification(record, [sys.executable, "-c", "print('184 passed in 0.01s')"],
                                  [self.source], self.directory)
        files = [self.source, record, record.with_suffix(".log")]
        self.manifest.write_text(json.dumps({"review_id": "cached-verification-fixture",
            "files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
            "verifications": [str(record)]}), encoding="utf-8")
        pack = self.pack(text="Full suite: 184 passed")
        first, _ = self.invoke(pack)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        future = time.time() + 60
        os.utime(self.source, (future, future))
        second, data = self.invoke(pack)
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_renaming_a_pack_cannot_reset_the_review_cycle(self):
        Proxy.response = report("待整改", blocker=True)
        first, _ = self.invoke(self.pack())
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        second, data = self.invoke(self.pack("renamed.md", "Revised evidence without previous receipt."))
        self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_truncated_stream_with_complete_json_text_is_not_approval(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("dispatch_stream_fixture", SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("Cannot load audit dispatch test fixture")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = 'event: response.output_text.delta\ndata: ' + json.dumps({
            "type": "response.output_text.delta", "delta": json.dumps(report(), ensure_ascii=False)
        }) + '\n\n'
        self.assertTrue(module.has_failed_response(raw))

    def test_incremental_review_must_close_every_old_blocker(self):
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(self.pack(text="ORIGINAL-LARGE-CONTENT"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        Proxy.response = report()
        second, data = self.invoke(self.pack("second.md", "Actual change and passing fixture."), first_data["receipt"])
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertFalse(data["gate_passed"])

    def test_incremental_prompt_uses_findings_not_the_entire_old_pack(self):
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(self.pack(text="ORIGINAL-LARGE-CONTENT"))
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        Proxy.response = report()
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": "protocol.md:1 and fixture.json::passed=true"}]
        second, data = self.invoke(self.pack("second.md", "Actual change and passing fixture."), first_data["receipt"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(data["round"], 2)
        self.assertIn("B1", Proxy.requests[-1]["input"])
        self.assertNotIn("ORIGINAL-LARGE-CONTENT", Proxy.requests[-1]["input"])

    def test_incremental_structured_evidence_survives_receipt_without_extra_round(self):
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(self.pack())
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        evidence = {"actual_evidence": "fixture passes", "path": "protocol.md"}
        Proxy.response = report()
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": evidence}]
        second, data = self.invoke(self.pack("second.md", "B1 corrected."), first_data["receipt"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        saved = json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(saved["report"]["closed_findings"][0]["evidence"]), evidence)
        self.assertEqual(saved["round"], 2)
        third, out = self.invoke(self.pack("third.md"), data["receipt"])
        self.assertEqual(third.returncode, 3)
        self.assertEqual(out["stage"], "review_limit")
        self.assertEqual(len(Proxy.requests), 2)

    def test_third_round_is_blocked_before_network_even_with_new_evidence(self):
        Proxy.response = report("待整改", blocker=True)
        _, first_data = self.invoke(self.pack())
        self.assertIn("receipt", first_data)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "未解决", "evidence": "fixture still fails"}]
        second, second_data = self.invoke(self.pack("second.md", "First attempted correction."), first_data["receipt"])
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        self.assertEqual(second_data["disposition"], "needs_user_decision")
        self.source.write_text("threshold = 0.2\n", encoding="utf-8")
        self.update_manifest()
        third, data = self.invoke(self.pack("third.md", "Another attempted correction."), second_data["receipt"])
        self.assertEqual(third.returncode, 3, third.stdout + third.stderr)
        self.assertEqual(data["stage"], "review_limit")
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 2)

    def two_round_stop(self):
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(self.pack())
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        Proxy.response = report("待整改", blocker=True)
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "未解决", "evidence": "fixture still fails"}]
        second, second_data = self.invoke(self.pack("second.md", "First attempted correction."), first_data["receipt"])
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        self.assertEqual(second_data["disposition"], "needs_user_decision")
        return pathlib.Path(second_data["receipt"])

    def decision_record(self, name, old_id, old_receipt, new_id, replace_sha=None):
        path = self.directory / name
        receipt_sha = hashlib.sha256(old_receipt.read_bytes()).hexdigest()
        path.write_text(
            "# 用户决定新周期\n\n"
            "用户原话：同意开启新周期，补充新证据后重审。\n"
            "决定时间：2026-11-14\n"
            "上一周期 review_id：%s\n"
            "上一周期最终回执 SHA256：%s\n"
            "新周期 review_id：%s\n"
            "未解决 blocking：B1\n"
            "变化原因：补充了新的阈值证据。\n"
            "不审范围：不重审已关闭项。\n"
            % (old_id, replace_sha or receipt_sha, new_id),
            encoding="utf-8",
        )
        return path

    def test_new_cycle_requires_explicit_entry_and_starts_at_round_one(self):
        receipt = self.two_round_stop()
        old = receipt.read_bytes()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "New cycle evidence with decision and old blocker summary.")
        Proxy.response = report()
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(data["cycle"], 2)
        self.assertEqual(data["round"], 1)
        self.assertEqual(receipt.read_bytes(), old)
        saved = json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["cycle"], 2)
        self.assertEqual(saved["round"], 1)
        self.assertIsNone(saved["previous"])
        self.assertEqual(saved["new_cycle"]["source_receipt_sha256"], hashlib.sha256(old).hexdigest())
        self.assertIn("用户决定的新周期", Proxy.requests[-1]["input"])

    def test_derived_review_id_without_new_cycle_flag_is_refused(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "Stealth cycle attempt.")
        before = len(Proxy.requests)
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("--new-cycle", data.get("error", ""))
        self.assertEqual(len(Proxy.requests), before)

    def test_new_cycle_requires_the_next_derived_id(self):
        receipt = self.two_round_stop()
        wrong_id = self.review_id + "__c3"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, wrong_id)
        self.update_manifest(extra=[record, receipt], review_id=wrong_id)
        pack = self.pack("cycle3.md", "Wrong cycle number.")
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("__c2", data.get("error", ""))

    def test_new_cycle_before_two_rounds_is_refused(self):
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(self.pack())
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        receipt = pathlib.Path(first_data["receipt"])
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "Too early for a new cycle.")
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("两轮结束", data.get("error", ""))

    def test_new_cycle_record_and_previous_receipt_must_be_in_inputs(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        pack = self.pack("cycle2.md", "Unbound cycle evidence.")
        self.update_manifest(review_id=new_id)
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("files", data.get("error", ""))
        self.update_manifest(extra=[record], review_id=new_id)
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("上一周期回执", data.get("error", ""))

    def test_new_cycle_record_must_reference_previous_receipt_sha(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id, replace_sha="0" * 64)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "Record with wrong receipt hash.")
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("SHA256", data.get("error", ""))

    def test_new_cycle_remediation_keeps_cycle_and_hits_two_round_limit(self):
        receipt = self.two_round_stop()
        old = receipt.read_bytes()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "New cycle evidence.")
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.assertEqual(first_data["cycle"], 2)
        self.assertEqual(first_data["round"], 1)
        self.source.write_text("threshold = 0.2\n", encoding="utf-8")
        self.update_manifest(review_id=new_id)
        Proxy.response = report("待整改", blocker=True)
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "未解决", "evidence": "fixture still fails"}]
        second, second_data = self.invoke(self.pack("cycle2-second.md", "New cycle correction."),
                                          first_data["receipt"])
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        self.assertEqual(second_data["cycle"], 2)
        self.assertEqual(second_data["round"], 2)
        self.assertEqual(second_data["disposition"], "needs_user_decision")
        saved = json.loads(pathlib.Path(second_data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["new_cycle"]["decision_record"], str(record))
        self.assertEqual(saved["new_cycle"]["source_receipt_sha256"], hashlib.sha256(old).hexdigest())
        self.assertEqual(receipt.read_bytes(), old)

    def test_derived_review_id_is_always_reserved_for_new_cycle(self):
        self.update_manifest(review_id=self.review_id + "__c2")
        pack = self.pack("reserved.md", "A free-form object with a cycle-shaped id.")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("--new-cycle", data.get("error", ""))
        self.assertEqual(Proxy.requests, [])

    def test_cycle_suffix_is_reserved_case_insensitively(self):
        self.update_manifest(review_id=self.review_id + "__C2")
        pack = self.pack("reserved-case.md", "A free-form object with an uppercase cycle suffix.")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("--new-cycle", data.get("error", ""))
        self.assertEqual(Proxy.requests, [])

    def test_new_cycle_record_field_must_be_on_one_line(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        text = record.read_text(encoding="utf-8").replace("上一周期 review_id：", "上一周期\nreview_id：")
        record.write_text(text, encoding="utf-8")
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "Split-line record field.")
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("上一周期 review_id", data.get("error", ""))

    def test_new_cycle_pack_with_old_conclusion_is_not_resubmitted(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack(
            "cycle2.md",
            "# Cycle\n\n## 5. 复核结论（fixture-review-model 于 2026-11-07 自动填写）\n旧意见。\n",
        )
        before = len(Proxy.requests)
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("旧包全文", data.get("error", ""))
        self.assertEqual(len(Proxy.requests), before)

    def test_new_cycle_record_must_name_previous_review_id_line(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        lines = [line for line in record.read_text(encoding="utf-8").splitlines()
                 if not line.startswith("上一周期 review_id")]
        record.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "Record without explicit previous id line.")
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("上一周期 review_id", data.get("error", ""))

    def test_new_cycle_requires_shared_controlled_object(self):
        receipt = self.two_round_stop()
        other = self.directory / "other-protocol.md"
        other.write_text("other = true\n", encoding="utf-8")
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.manifest.write_text(json.dumps({
            "review_id": new_id,
            "files": [
                {"path": str(other), "sha256": hashlib.sha256(other.read_bytes()).hexdigest()},
                {"path": str(record), "sha256": hashlib.sha256(record.read_bytes()).hexdigest()},
                {"path": str(receipt), "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()},
            ],
        }), encoding="utf-8")
        pack = self.pack("cycle2.md", "Unrelated object evidence.")
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("共同的受控原件", data.get("error", ""))

    def test_new_cycle_after_first_round_rejection_is_allowed(self):
        Proxy.response = report("不通过", blocker=True)
        first, first_data = self.invoke(self.pack())
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.assertEqual(first_data["disposition"], "rejected_stop")
        receipt = pathlib.Path(first_data["receipt"])
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        pack = self.pack("cycle2.md", "New cycle after first-round rejection.")
        Proxy.response = report()
        run, data = self.invoke(pack, receipt, extra=["--new-cycle", str(record)])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(data["cycle"], 2)
        self.assertEqual(data["round"], 1)

    def test_second_new_cycle_uses_next_root_number(self):
        receipt = self.two_round_stop()
        new_id = self.review_id + "__c2"
        record = self.decision_record("new-cycle.md", self.review_id, receipt, new_id)
        self.update_manifest(extra=[record, receipt], review_id=new_id)
        Proxy.response = report("待整改", blocker=True)
        first, first_data = self.invoke(self.pack("cycle2.md", "New cycle evidence."), receipt,
                                        extra=["--new-cycle", str(record)])
        self.assertEqual(first.returncode, 3, first.stdout + first.stderr)
        self.assertEqual(first_data["cycle"], 2)
        self.source.write_text("threshold = 0.3\n", encoding="utf-8")
        self.update_manifest(review_id=new_id)
        Proxy.response = report("待整改", blocker=True)
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "未解决", "evidence": "still fails"}]
        second, second_data = self.invoke(self.pack("cycle2-second.md", "Cycle 2 correction."),
                                          first_data["receipt"])
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        self.assertEqual(second_data["cycle"], 2)
        self.assertEqual(second_data["round"], 2)
        cycle3_id = self.review_id + "__c3"
        cycle2_receipt = pathlib.Path(second_data["receipt"])
        record3 = self.decision_record("new-cycle-3.md", new_id, cycle2_receipt, cycle3_id)
        self.update_manifest(extra=[record3, cycle2_receipt], review_id=cycle3_id)
        pack3 = self.pack("cycle3.md", "Third cycle evidence.")
        Proxy.response = report()
        third, third_data = self.invoke(pack3, cycle2_receipt, extra=["--new-cycle", str(record3)])
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertEqual(third_data["cycle"], 3)
        self.assertEqual(third_data["round"], 1)

    def test_two_substantive_material_incomplete_reports_also_stop_for_user_decision(self):

        Proxy.response = report("未完成")
        first, first_data = self.invoke(self.pack(text="Current evidence lacks a source field."))
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        second, data = self.invoke(self.pack("second.md", "The requested source field remains unavailable."), first_data["receipt"])
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertEqual(data["round"], 2)
        self.assertTrue(data["review_counted"])
        self.assertEqual(data["disposition"], "needs_user_decision")
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 2)

    def test_unbound_source_snippet_stops_before_model_call(self):
        run, data = self.invoke(self.pack(text="Original source:\n```python\nx = {{1: 2}}\n```\n"))
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "pack_lint")
        self.assertEqual(Proxy.requests, [])

    def test_truncation_default_stops_without_a_substantive_round(self):
        Proxy.incomplete_remaining = 3
        pack = self.pack()
        original = pack.read_bytes()
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["failure_kind"], "response_truncated")
        self.assertFalse(data["review_counted"])
        self.assertEqual(data["disposition"], "response_incomplete")
        self.assertEqual(len(Proxy.requests), 1)
        self.assertEqual(pack.read_bytes(), original)
        saved = json.loads(pathlib.Path(data["attempt_receipts"][0]).read_text(encoding="utf-8"))
        self.assertEqual(saved["upstream"]["response_id"], "resp_cut")
        self.assertFalse(pathlib.Path(str(pack) + ".review.json").exists())

    def test_explicit_truncation_retry_uses_budget_once_and_counts_one_review(self):
        Proxy.incomplete_remaining = 1
        run, data = self.invoke(self.pack(), extra=["--max-output-tokens", "12000", "--retry-truncated-tokens", "24000"])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual([r["max_output_tokens"] for r in Proxy.requests], [12000, 24000])
        self.assertEqual(data["round"], 1)
        saved = json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["attempt_receipts"]), 1)
        self.assertEqual(saved["output_budget"]["effective_cap"], 24000)

    def test_truncation_retry_is_bounded_even_when_second_response_is_cut(self):
        Proxy.incomplete_remaining = 4
        run, data = self.invoke(self.pack(), extra=["--max-output-tokens", "12000", "--retry-truncated-tokens", "24000"])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(len(Proxy.requests), 2)
        self.assertEqual(len(data["attempt_receipts"]), 2)
        self.assertFalse(data["review_counted"])

    def test_invalid_retry_budget_is_rejected_locally(self):
        run, data = self.invoke(self.pack(), extra=["--retry-truncated-tokens", "12000"])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "args")
        self.assertEqual(Proxy.requests, [])

    def test_dependency_drift_during_review_prevents_append_and_approval(self):
        pack = self.pack()
        original = pack.read_bytes()
        Proxy.callbacks["on_request"] = lambda: self.source.write_text("concurrent change\n", encoding="utf-8")
        run, data = self.invoke(pack)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(pack.read_bytes(), original)

    def test_invalid_report_is_not_a_baseline_and_is_not_a_review_round(self):
        Proxy.response = "not a JSON review"
        first, data = self.invoke(self.pack())
        self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
        saved = json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["round"], 0)
        self.assertFalse(saved["review_counted"])
        self.assertEqual(saved["raw_report"], "not a JSON review")
        Proxy.response = report()
        second, result = self.invoke(self.pack("second.md", "Only a format recovery, no new evidence."), data["receipt"])
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertEqual(result["stage"], "binding")
        self.assertEqual(len(Proxy.requests), 1)

    def test_default_output_budget_is_larger_and_explicitly_overridable(self):
        first, _ = self.invoke(self.pack())
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(Proxy.requests[-1]["max_output_tokens"], 32768)
        self.source.write_text("different review object\n", encoding="utf-8")
        self.manifest.write_text(json.dumps({
            "review_id": "second-output-budget",
            "files": [{"path": str(self.source), "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}],
        }), encoding="utf-8")
        second, _ = self.invoke(self.pack("other.md"), extra=["--max-output-tokens", "48000"])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(Proxy.requests[-1]["max_output_tokens"], 48000)

    def test_structured_repair_test_is_preserved_in_valid_review(self):
        value = report("未完成", blocker=True)
        value["findings"][0].update(domain="implementation", repair={
            "status": "patch_proposed", "reason": "Source is sufficient.",
            "patch": "patch for fixture", "test_case": {"input": "fixture", "expected": "reject"},
            "targets": [{"path": "protocol.md", "sha256": "a" * 64}],
        })
        Proxy.response = value
        run, data = self.invoke(self.pack())
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        receipt = json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["round"], 1)
        self.assertEqual(receipt["report"]["decision"], "未完成")
        self.assertEqual(json.loads(receipt["report"]["findings"][0]["repair"]["test_case"]),
                         {"input": "fixture", "expected": "reject"})
        self.assertFalse(data["gate_passed"])

    def test_legacy_format_recovery_keeps_original_and_enforces_old_blockers(self):
        value = report("未完成", blocker=True)
        value["findings"][0].update(domain="implementation", repair={
            "status": "patch_proposed", "reason": "Source is sufficient.",
            "patch": "patch for fixture", "test_case": {"input": "fixture", "expected": "reject"},
            "targets": [{"path": "protocol.md", "sha256": "a" * 64}],
        })
        Proxy.response = value
        _, first = self.invoke(self.pack())
        old_path = pathlib.Path(first["receipt"])
        old = json.loads(old_path.read_text(encoding="utf-8"))
        old.update(round=0, review_counted=False, report=None,
                   error="裁决合同未通过: repair补丁缺少patch/test_case")
        old["binding"]["rules_sha256"] = "8ae413ca0983351c65206f03c1c7b46e3b4db5b873132ca54ac14886016f62ac"
        old_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        self.register_fixture_as_historical()
        before = old_path.read_bytes()
        self.source.write_text("fixed fixture\n", encoding="utf-8")
        self.update_manifest()
        pack = self.pack("second.md", "Correction of B1 and fixture result.")
        run, data = self.invoke(pack, old_path, extra=["--recover-previous-format", "--dry-run"])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(data["contract"]["active_blocker_ids"], ["B1"])
        self.assertFalse(data["gate_passed"])
        self.assertEqual(data["recovered_previous"]["original_round"], 0)
        self.assertEqual(data["recovered_previous"]["effective_round"], 1)
        self.assertTrue(data["recovered_previous"]["rules_changed"])
        self.assertEqual(old_path.read_bytes(), before)
        self.assertEqual(len(Proxy.requests), 1)

        with tempfile.TemporaryDirectory() as other:
            other_pack = pathlib.Path(other) / "increment.md"
            other_pack.write_text("A different review directory.", encoding="utf-8")
            run, data = self.invoke(other_pack, old_path, extra=["--recover-previous-format", "--dry-run"])
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertEqual(data["stage"], "review_scope")
            self.assertEqual(len(Proxy.requests), 1)

        Proxy.response = report()
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": "fixture passes"}]
        run, data = self.invoke(self.pack("third.md", "B1 fixed and fixture verified."),
                                old_path, extra=["--recover-previous-format"])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(data["round"], 2)
        saved = json.loads(pathlib.Path(data["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["recovered_previous"]["source_sha256"], hashlib.sha256(before).hexdigest())
        self.assertTrue(saved["recovered_previous"]["rules_changed"])
        self.assertEqual(old_path.read_bytes(), before)
        attempts = len(Proxy.requests)
        run, data = self.invoke(self.pack("fourth.md", "Another correction."),
                                old_path, extra=["--recover-previous-format"])
        self.assertEqual(run.returncode, 3, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "review_limit")
        self.assertEqual(len(Proxy.requests), attempts)

    def test_format_recovery_rejects_modified_raw_report_and_unknown_failures(self):
        value = report("未完成", blocker=True)
        value["findings"][0].update(domain="implementation", repair={
            "status": "patch_proposed", "reason": "Source is sufficient.",
            "patch": "patch for fixture", "test_case": {"input": "fixture", "expected": "reject"},
            "targets": [{"path": "protocol.md", "sha256": "a" * 64}],
        })
        Proxy.response = value
        _, first = self.invoke(self.pack())
        old_path = pathlib.Path(first["receipt"])
        old = json.loads(old_path.read_text(encoding="utf-8"))
        old.update(round=0, review_counted=False, report=None,
                   error="裁决合同未通过: repair补丁缺少patch/test_case")
        old["binding"]["rules_sha256"] = "8ae413ca0983351c65206f03c1c7b46e3b4db5b873132ca54ac14886016f62ac"
        old["raw_report"] = old["raw_report"].replace("Source is sufficient.", "altered evidence")
        old_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        self.source.write_text("fixed fixture\n", encoding="utf-8")
        self.update_manifest()
        pack = self.pack("second.md", "Correction of B1.")
        run, data = self.invoke(pack, old_path, extra=["--recover-previous-format", "--dry-run"])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "binding")
        self.assertEqual(len(Proxy.requests), 1)

        old["error"] = "another unrelated format failure"
        old_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        run, data = self.invoke(pack, old_path, extra=["--recover-previous-format", "--dry-run"])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "binding")
        self.assertEqual(len(Proxy.requests), 1)

    def test_compatible_older_rules_continue_without_losing_blockers(self):
        Proxy.response = report("待整改", blocker=True)
        _, first = self.invoke(self.pack())
        receipt = pathlib.Path(first["receipt"])
        old = json.loads(receipt.read_text(encoding="utf-8"))
        old["binding"]["rules_sha256"] = "8ae413ca0983351c65206f03c1c7b46e3b4db5b873132ca54ac14886016f62ac"
        receipt.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        self.register_fixture_as_historical()
        before = receipt.read_bytes()
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        run, data = self.invoke(self.pack("second.md", "B1: correction and fixture result."), receipt, extra=["--dry-run"])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(data["contract"]["active_blocker_ids"], ["B1"])
        self.assertEqual(data["rules_transition"]["previous_rules_sha256"], old["binding"]["rules_sha256"])
        self.assertEqual(receipt.read_bytes(), before)
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)
        Proxy.response = report()
        Proxy.response["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": "fixture passes"}]
        second, result = self.invoke(self.directory / "second.md", receipt)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        saved = json.loads(pathlib.Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["round"], 2)
        self.assertEqual(saved["rules_transition"]["previous_rules_sha256"], old["binding"]["rules_sha256"])
        self.assertEqual(receipt.read_bytes(), before)
        third, out = self.invoke(self.pack("third.md", "Another correction"), result["receipt"])
        self.assertEqual(third.returncode, 3, third.stdout + third.stderr)
        self.assertEqual(out["stage"], "review_limit")
        self.assertEqual(len(Proxy.requests), 2)

    def test_real_architecture_baseline_receipt_preserves_lifecycle(self):
        def mapping(value: object) -> dict[str, object]:
            if not isinstance(value, dict):
                raise ValueError("Expected a JSON object from the migration fixture")
            return cast(dict[str, object], value)

        def receipt_path(data: dict[str, object]) -> pathlib.Path:
            path = data["receipt"]
            if not isinstance(path, str):
                raise ValueError("Missing migration receipt path")
            return pathlib.Path(path)

        def request_count() -> int:
            requests = cast(object, vars(Proxy)["requests"])
            if not isinstance(requests, list):
                raise ValueError("Invalid loopback request capture")
            return len(cast(list[object], requests))

        def invoke(script: pathlib.Path, pack: pathlib.Path, previous: pathlib.Path | None = None,
                   *, dry_run: bool = False) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
            command = [sys.executable, "-B", str(script), "--pack", str(pack), "--point", "1",
                       "--port", str(self.server.server_port), "--no-stream", "--inputs", str(self.manifest)]
            if previous is not None:
                command.extend(["--previous", str(previous)])
            if dry_run:
                command.append("--dry-run")
            run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)
            return run, mapping(cast(object, json.loads(run.stdout)))

        old_root = os.environ.get("RESEARCH_ARCHITECTURE_OLD_ROOT")
        if not old_root:
            self.skipTest("Migration test requires the verified pre-refactor snapshot")
        old_script = pathlib.Path(old_root) / "scripts" / "audit_dispatch.py"
        Proxy.response = report("待整改", blocker=True)
        first_run, first = invoke(old_script, self.pack())
        self.assertEqual(first_run.returncode, 3, first_run.stdout + first_run.stderr)
        first_binding = mapping(first["binding"])
        self.assertEqual(first_binding["rules_sha256"],
            "32a00a6aaa344e5c10a5a31eec984f3386e3a226f4da27e0273fe7f860dbdd34")
        receipt = receipt_path(first)
        before = receipt.read_bytes()
        _ = self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        pack = self.pack("migration.md", "B1: actual correction and passing fixture.")
        preflight, current = invoke(SCRIPT, pack, receipt, dry_run=True)
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
        contract = mapping(current["contract"])
        self.assertEqual(contract["active_blocker_ids"], ["B1"])
        self.assertFalse(current["gate_passed"])
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual(request_count(), 1)
        response = report()
        response["closed_findings"] = [{"id": "B1", "status": "已解决", "evidence": "fixture passes"}]
        Proxy.response = response
        second_run, second = invoke(SCRIPT, pack, receipt)
        self.assertEqual(second_run.returncode, 0, second_run.stdout + second_run.stderr)
        saved = mapping(cast(object, json.loads(receipt_path(second).read_text(encoding="utf-8"))))
        self.assertEqual(saved["round"], 2)
        third_run, third = invoke(SCRIPT, self.pack("third.md"), receipt_path(second))
        self.assertEqual(third_run.returncode, 3)
        self.assertEqual(third["stage"], "review_limit")
        self.assertEqual(receipt.read_bytes(), before)
        evidence = os.environ.get("RESEARCH_ARCHITECTURE_MIGRATION_EVIDENCE")
        if evidence:
            _ = pathlib.Path(evidence).write_text(json.dumps({
                "previous_rules_sha256": first_binding["rules_sha256"],
                "rules_transition": current["rules_transition"],
                "active_blocker_ids": contract["active_blocker_ids"],
                "preflight_gate_passed": current["gate_passed"],
                "original_receipt_unchanged": receipt.read_bytes() == before,
                "second_round": saved["round"], "third_stage": third["stage"],
                "model_requests": request_count(), "transport": "loopback fixture, not a real model",
            }, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_unknown_old_rules_are_not_automatically_accepted(self):
        Proxy.response = report("待整改", blocker=True)
        _, first = self.invoke(self.pack())
        receipt = pathlib.Path(first["receipt"])
        old = json.loads(receipt.read_text(encoding="utf-8"))
        old["binding"]["rules_sha256"] = "f" * 64
        receipt.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        run, data = self.invoke(self.pack("second.md", "B1: correction and fixture result."), receipt, extra=["--dry-run"])
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(data["stage"], "binding")
        self.assertEqual(len(Proxy.requests), 1)

    def test_dry_run_exports_active_ids_and_checks_contract_without_a_request(self):
        Proxy.response = report("待整改", blocker=True)
        _, first = self.invoke(self.pack())
        self.source.write_text("threshold = 0.1\n", encoding="utf-8")
        self.update_manifest()
        pack = self.pack("second.md", "B1: actual correction and fixture result.")
        run = subprocess.run([sys.executable, "-B", str(SCRIPT), "--pack", str(pack), "--point", "1",
                              "--inputs", str(self.manifest), "--previous", first["receipt"], "--dry-run"],
                             capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual(data["contract"]["active_blocker_ids"], ["B1"])
        self.assertTrue(data["schema_validated"])
        self.assertFalse(data["gate_passed"])
        self.assertEqual(len(Proxy.requests), 1)

    def test_upstream_incomplete_reason_is_preserved(self):
        class IncompleteProxy(Proxy):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"id": "resp_fixture", "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"}}).encode("utf-8"))

        server = http.server.HTTPServer(("127.0.0.1", 0), IncompleteProxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        old_server = type(self)._shared_server
        type(self)._shared_server = server
        try:
            run, data = self.invoke(self.pack())
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            self.assertEqual(data["upstream"]["reason"], "max_output_tokens")
            self.assertEqual(data["upstream"]["response_id"], "resp_fixture")
            self.assertFalse(data["gate_passed"])
        finally:
            type(self)._shared_server = old_server
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
