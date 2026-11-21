"""Evidence-bound recovery; every test uses an isolated ledger, never the network."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import review_ledger as ledger


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def case(tmp_path):
    if os.name == "nt":
        tmp_path = Path(ledger._long_path(tmp_path))
    project = tmp_path / "research" / "project-991-recovery"
    reviews = project / "01-资料" / "reviews"
    reviews.mkdir(parents=True)
    pack = reviews / "original.md"
    pack.write_bytes(b"original bound package")
    key = hashlib.sha256(json.dumps(["recovery-test", 1], ensure_ascii=False).encode()).hexdigest()
    lock = reviews / (".review-send-" + key + ".lock")
    lock.write_text(json.dumps({"pid": 4294967294, "pack": str(pack),
                               "review_id": "recovery-test", "point": 1}), encoding="utf-8")
    moment = datetime.datetime(2026, 11, 19, 10, tzinfo=datetime.timezone.utc)
    os.utime(lock, (moment.timestamp(), moment.timestamp()))
    attempt = Path(str(pack) + ".attempt-test.json")
    data = {"schema": "research-session/review-attempt/v1",
            "stage": "upstream", "failure_kind": "http_error", "status": 400,
            "ok": False, "disposition": "incomplete_stop",
            "upstream": {"code": "model_not_found", "type": "invalid_request_error"},
            "review_counted": False, "review_completed": False, "gate_passed": False,
            "created_at": (moment + datetime.timedelta(seconds=2)).isoformat(),
            "binding": {"review_id": "recovery-test", "point": 1, "pack_sha256": digest(pack)}}
    attempt.write_text(json.dumps(data), encoding="utf-8")
    scope = {"project_id": ledger.project_identity(project), "project_root": str(project),
             "canonical": str(reviews)}
    home = str(tmp_path / "ledger")
    intent = ledger.send_intent(scope, data["binding"], str(pack), 1)
    intent["created_at"] = (moment + datetime.timedelta(seconds=1)).isoformat()
    rows = ledger.append_entries(home, [
        ledger.coverage_entry(scope, [], [], []), intent,
        ledger.attempt_entry(scope, data["binding"], str(attempt), "http_error", 1),
        ledger._lock_record(str(lock), scope["project_id"], str(project))])
    ledger.resolve_intent(home, rows[1]["entry_id"], "verified terminal rejection")
    c = dict(project=project, reviews=reviews, pack=pack, lock=lock, attempt=attempt,
             home=home, scope=scope, target=rows[3]["entry_id"], data=data, moment=moment)
    assess(c)
    return c


def assess(c, **changes):
    value = ledger.send_lock_recovery_template(c["home"], c["target"])
    original = c["project"] / "independent-review.txt"
    original.write_text("Independent assessment of this failed send; no valid review; limitations retained.",
                        encoding="utf-8")
    value.update(decision="recover_failed_send", outcome="not_accepted",
                 operator="test-operator", reviewer="independent-test-reviewer",
                 reviewed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 basis="Bound terminal gateway rejection", limitations="No scientific approval",
                 review_record={"path": str(original), "sha256": digest(original)})
    value.update(changes)
    c["evidence"] = c["project"] / "recovery.json"
    c["evidence"].write_text(json.dumps(value), encoding="utf-8")
    return value


def recover(c, **extra):
    return ledger.recover_send_lock(c["home"], c["target"], str(c["evidence"]),
                                    digest(c["evidence"]), **extra)


def summary(c):
    return ledger.preflight(c["home"], c["scope"], "recovery-test", 1)


def test_recovery_preserves_evidence_and_clears_only_failed_lock(case):
    before = Path(ledger.ledger_path(case["home"])).read_bytes()
    original = case["lock"].read_bytes()
    assert len(summary(case)["unknown_locks"]) == 1
    with mock.patch.object(ledger, "_pid_started_at", return_value=None):
        result = recover(case)
    assert not case["lock"].exists()
    assert Path(result["archive"]).read_bytes() == original
    assert Path(ledger.ledger_path(case["home"])).read_bytes().startswith(before)
    state = summary(case)
    assert state["unknown_locks"] == []
    assert len(state["attempts"]) == 1
    assert state["canonical_receipts"] == []
    assert ledger.blocking_reason(state) is None
    assert result["gate_passed"] is False
    assert result["review_completed"] is False


def test_dry_run_writes_nothing(case):
    before = Path(ledger.ledger_path(case["home"])).read_bytes()
    with mock.patch.object(ledger, "_pid_started_at", return_value=None):
        result = recover(case, dry_run=True)
    assert case["lock"].exists()
    assert not Path(result["archive"]).exists()
    assert Path(ledger.ledger_path(case["home"])).read_bytes() == before
    assert len(summary(case)["unknown_locks"]) == 1


@pytest.mark.parametrize("change", [
    {"review_completed": True},
    {"review_counted": True}, {"gate_passed": True},
    {"binding": {"review_id": "other", "point": 1}},
    {"binding": {"review_id": "recovery-test", "point": 2}},
])
def test_ambiguous_or_cross_object_evidence_is_rejected(case, change):
    data = {**case["data"], **change}
    case["attempt"].write_text(json.dumps(data), encoding="utf-8")
    before = Path(ledger.ledger_path(case["home"])).read_bytes()
    with mock.patch.object(ledger, "_pid_started_at", return_value=None), pytest.raises(ValueError):
        recover(case)
    assert case["lock"].exists()
    assert Path(ledger.ledger_path(case["home"])).read_bytes() == before


@pytest.mark.parametrize("which", ["lock", "attempt"])
def test_stale_expected_hash_is_rejected(case, which):
    case[which].write_bytes(case[which].read_bytes() + b" ")
    with mock.patch.object(ledger, "_pid_started_at", return_value=None), pytest.raises(ValueError):
        recover(case)
    assert case["lock"].exists()


def test_live_original_owner_is_rejected(case):
    with mock.patch.object(ledger, "_pid_started_at",
                           return_value=case["moment"].timestamp() - 100), pytest.raises(ValueError):
        recover(case)
    assert case["lock"].exists()


def test_inaccessible_owner_is_rejected(case):
    with mock.patch.object(ledger, "_pid_started_at", side_effect=ValueError("access denied")), \
            pytest.raises(ValueError):
        recover(case)
    assert case["lock"].exists()


def test_reused_pid_can_be_recovered(case):
    with mock.patch.object(ledger, "_pid_started_at",
                           return_value=case["moment"].timestamp() + 100):
        assess(case)
        result = recover(case)
    assert result["owner_state"] == "pid_reused"
    assert not case["lock"].exists()


def test_new_lock_at_same_path_remains_blocking(case):
    with mock.patch.object(ledger, "_pid_started_at", return_value=None):
        recover(case)
    case["lock"].write_text(json.dumps({"pid": 23456, "pack": str(case["pack"]),
                                      "review_id": "recovery-test", "point": 1}), encoding="utf-8")
    state = summary(case)
    assert len(state["unknown_locks"]) == 1
    assert ledger.blocking_reason(state)[0] == "review_history"


def test_archive_tampering_invalidates_resolution(case):
    with mock.patch.object(ledger, "_pid_started_at", return_value=None):
        result = recover(case)
    Path(result["archive"]).write_bytes(b"changed evidence")
    assert len(summary(case)["unknown_locks"]) >= 1


def test_unverified_generic_resolution_cannot_clear_lock(case):
    ledger.append_entries(case["home"], [
        {**case["scope"], "kind": "resolution", "review_id": "recovery-test",
         "point": 1, "resolves": case["target"], "note": "blindly deleted"}])
    case["lock"].unlink()
    assert len(summary(case)["unknown_locks"]) == 1


def test_cli_recovery_dry_run_never_creates_send_lock_or_request(case):
    command = [sys.executable, "-B", str(ROOT / "scripts" / "audit_dispatch.py"),
               "--recover-send-lock", case["target"], "--ledger-dir", case["home"],
               "--recovery-evidence", str(case["evidence"]),
               "--recovery-sha256", digest(case["evidence"]), "--dry-run"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["local_only"] is True
    assert value["gate_passed"] is False
    assert value["applied"] is False
    assert case["lock"].exists()


def test_current_process_creation_time_is_observable():
    if os.name != "nt":
        pytest.skip("Windows creation-time API")
    assert 0 < ledger._pid_started_at(os.getpid()) <= datetime.datetime.now().timestamp()


@pytest.mark.parametrize("code", ["invalid_request", "permission_denied", "rate_limit_exceeded"])
def test_recovery_is_not_limited_to_model_not_found(case, code):
    data = case["data"]
    data.update(status=429 if code == "rate_limit_exceeded" else 400,
                upstream={"code": code, "type": "invalid_request_error"})
    case["attempt"].write_text(json.dumps(data), encoding="utf-8")
    # A previously registered SHA is intentionally immutable. Start with a legacy row
    # to model pre-v4.0.3 diagnostics without making a stale SHA acceptable.
    with mock.patch.object(ledger, "_file_sha256", wraps=ledger._file_sha256):
        rows = ledger.read_entries(case["home"])
    # Use a fresh isolated history; this fixture edit is test setup, never production recovery.
    home = str(case["project"].parent.parent / ("legacy-ledger-" + code))
    for row in rows:
        if row["kind"] == "attempt":
            row.pop("attempt_sha256", None)
    ledger.append_entries(home, rows)
    case["home"] = home
    assess(case)
    recover(case)
    assert summary(case)["unknown_locks"] == []


@pytest.mark.parametrize("failure", ["transport", "response_truncated", "http_500", "http_408"])
def test_uncertain_failure_requires_terminal_confirmation(case, failure):
    data = case["data"]
    data.update(failure_kind="http_error" if failure.startswith("http") else failure,
                status=int(failure[5:]) if failure.startswith("http") else None,
                upstream={"code": "timeout", "type": "server_error"})
    case["attempt"].write_text(json.dumps(data), encoding="utf-8")
    rows = ledger.read_entries(case["home"])
    for row in rows:
        if row["kind"] == "attempt":
            row.pop("attempt_sha256", None)
    home = str(case["project"].parent.parent / ("uncertain-ledger-" + failure))
    ledger.append_entries(home, rows)
    case["home"] = home
    value = assess(case)
    with pytest.raises(ValueError, match="terminal confirmation"):
        recover(case)
    terminal = case["project"] / "gateway-terminal.json"
    terminal.write_text(json.dumps({"request": "req-fixture", "terminal": "failed",
                                    "review": None}), encoding="utf-8")
    assess(case, outcome="terminated_without_valid_review", upstream_confirmation={
        "terminal_state": "failed", "intent_id": value["target"]["intent_id"],
        "no_valid_review": True, "request_identity": "req-fixture",
        "source_kind": "gateway", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "record": {"path": str(terminal), "sha256": digest(terminal)}})
    recover(case)
    assert summary(case)["unknown_locks"] == []


def test_recovery_resumes_after_unlink_before_completion(case):
    original = ledger._append_locked

    def crash(home, path, values):
        if any(row.get("resolution_type") == "send_lock_recovered" for row in values):
            raise OSError("simulated crash after unlink")
        return original(home, path, values)

    with mock.patch.object(ledger, "_append_locked", side_effect=crash), pytest.raises(OSError):
        recover(case)
    assert not case["lock"].exists()
    assert len(summary(case)["unknown_locks"]) >= 1
    result = recover(case)
    assert result["applied"] is True
    assert summary(case)["unknown_locks"] == []
    assert recover(case)["already_completed"] is True


def test_revocation_restores_blocking_without_rewriting_history(case):
    recover(case)
    prepared = next(row for row in ledger.read_entries(case["home"])
                    if row.get("resolution_type") == "send_lock_prepared")
    ledger.revoke_send_recovery(case["home"], prepared["recovery_id"], "new contradictory evidence")
    assert ledger.blocking_reason(summary(case))[0] == "review_history"


def test_partial_completion_is_finished_on_resume(case):
    original = ledger._append_locked

    def crash(home, path, values):
        if len(values) > 1 and values[0].get("resolution_type") == "send_lock_recovered":
            original(home, path, values[:1])
            raise OSError("simulated crash between completion rows")
        return original(home, path, values)

    with mock.patch.object(ledger, "_append_locked", side_effect=crash), pytest.raises(OSError):
        recover(case)
    recover(case)
    assert summary(case)["unknown_locks"] == []
    completed = [row["resolves"] for row in ledger.read_entries(case["home"])
                 if row.get("resolution_type") == "send_lock_recovered"]
    target = json.loads(case["evidence"].read_text(encoding="utf-8"))["target"]
    assert set(completed) == {*target["lock_entry_ids"], target["intent_entry_id"]}


def test_revoked_preparation_cannot_remove_the_lock(case):
    original = ledger._append_locked

    def crash(home, path, values):
        result = original(home, path, values)
        if any(row.get("resolution_type") == "send_lock_prepared" for row in values):
            raise OSError("simulated crash before archival")
        return result

    with mock.patch.object(ledger, "_append_locked", side_effect=crash), pytest.raises(OSError):
        recover(case)
    prepared = next(row for row in ledger.read_entries(case["home"])
                    if row.get("resolution_type") == "send_lock_prepared")
    ledger.revoke_send_recovery(case["home"], prepared["recovery_id"], "retracted assessment")
    with pytest.raises(ValueError, match="revoked"):
        recover(case)
    assert case["lock"].exists()


def test_recovery_resumes_after_preparation_before_archival(case):
    original = ledger._append_locked

    def crash(home, path, values):
        result = original(home, path, values)
        if any(row.get("resolution_type") == "send_lock_prepared" for row in values):
            raise OSError("simulated crash before archival")
        return result

    with mock.patch.object(ledger, "_append_locked", side_effect=crash), pytest.raises(OSError):
        recover(case)
    recover(case)
    assert summary(case)["unknown_locks"] == []


def test_pid_only_self_skip_no_longer_hides_old_lock(case):
    data = json.loads(case["lock"].read_text(encoding="utf-8"))
    data["pid"] = os.getpid()
    case["lock"].write_text(json.dumps(data), encoding="utf-8")
    assert ledger._lock_record(str(case["lock"]), case["scope"]["project_id"],
                               str(case["project"]), skip_pid=os.getpid()) is not None


def test_ledger_mutex_can_be_reclaimed_after_owner_exits(case):
    if os.name != "nt":
        pytest.skip("Windows kernel mutex recovery")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import review_ledger as l; "
            "l._acquire(sys.argv[2]); sys.exit(0)")
    result = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "scripts"),
                             ledger.ledger_path(case["home"])], capture_output=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0
    mutex = ledger._acquire(ledger.ledger_path(case["home"]), timeout=0.2)
    ledger._release_ledger(mutex)


def test_ledger_mutex_never_removes_a_live_legacy_owner(case):
    path = ledger.ledger_path(case["home"])
    legacy = Path(path + ".lock")
    legacy.write_text(json.dumps({"pid": os.getpid(), "created_at": ledger.utc_now()}), encoding="ascii")
    before = legacy.read_bytes()
    with pytest.raises(ValueError):
        ledger._acquire(path, timeout=0.1)
    assert legacy.read_bytes() == before
    legacy.unlink()


def test_resume_rechecks_new_intent_after_unlink(case):
    original = ledger._append_locked

    def crash(home, path, values):
        if any(row.get("resolution_type") == "send_lock_recovered" for row in values):
            raise OSError("simulated crash after unlink")
        return original(home, path, values)

    with mock.patch.object(ledger, "_append_locked", side_effect=crash), pytest.raises(OSError):
        recover(case)
    ledger.append_entries(case["home"], [
        ledger.send_intent(case["scope"], case["data"]["binding"], str(case["pack"]), 1)])
    with pytest.raises(ValueError, match="ambiguous|later"):
        recover(case)


def test_normal_package_append_does_not_invalidate_completed_recovery(case):
    recover(case)
    case["pack"].write_bytes(case["pack"].read_bytes() + b"\nnew review appended normally\n")
    assert ledger.blocking_reason(summary(case)) is None


def test_missing_attempt_needs_request_specific_terminal_evidence(case):
    rows = [row for row in ledger.read_entries(case["home"]) if row["kind"] != "attempt"]
    case["attempt"].unlink()
    case["home"] = str(case["project"].parent.parent / "no-attempt-ledger")
    ledger.append_entries(case["home"], rows)
    value = assess(case)
    with pytest.raises(ValueError, match="terminal confirmation"):
        recover(case)
    terminal = case["project"] / "terminal-without-local-attempt.json"
    terminal.write_text(json.dumps({"intent_id": value["target"]["intent_id"],
                                    "request": "request-missing-local-diagnostic",
                                    "terminal": "failed", "review": None}), encoding="utf-8")
    assess(case, outcome="terminated_without_valid_review", upstream_confirmation={
        "terminal_state": "failed", "intent_id": value["target"]["intent_id"],
        "no_valid_review": True, "request_identity": "request-missing-local-diagnostic",
        "source_kind": "gateway", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "record": {"path": str(terminal), "sha256": digest(terminal)}})
    recover(case)
    assert ledger.blocking_reason(summary(case)) is None


def test_modern_guard_before_intent_requires_terminal_evidence(case):
    rows = [row for row in ledger.read_entries(case["home"])
            if row["kind"] not in {"attempt", "send_intent", "lock", "resolution"}]
    case["attempt"].unlink()
    data = json.loads(case["lock"].read_text(encoding="utf-8"))
    data.update(guard_id="unclaimed-guard", created_at=case["moment"].isoformat(),
                pack_sha256=digest(case["pack"]))
    case["lock"].write_text(json.dumps(data), encoding="utf-8")
    os.utime(case["lock"], (case["moment"].timestamp(), case["moment"].timestamp()))
    case["home"] = str(case["project"].parent.parent / "pre-intent-ledger")
    rows.append(ledger._lock_record(str(case["lock"]), case["scope"]["project_id"], str(case["project"])))
    case["target"] = ledger.append_entries(case["home"], rows)[-1]["entry_id"]
    value = assess(case)
    assert value["target"]["intent_entry_id"] is None
    with pytest.raises(ValueError, match="terminal confirmation"):
        recover(case)
    terminal = case["project"] / "unclaimed-guard-confirmation.json"
    terminal.write_text(json.dumps({"guard_id": "unclaimed-guard", "accepted": False}), encoding="utf-8")
    assess(case, upstream_confirmation={
        "terminal_state": "rejected", "intent_id": None, "guard_id": "unclaimed-guard",
        "no_valid_review": True, "request_identity": "unclaimed-guard",
        "source_kind": "gateway", "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "record": {"path": str(terminal), "sha256": digest(terminal)}})
    recover(case)
    assert ledger.blocking_reason(summary(case)) is None


def test_normal_release_rejects_a_modified_guard(case):
    case["lock"].unlink()
    guard = ledger.create_send_guard(case["home"], case["scope"], str(case["lock"]),
                                    {"pid": os.getpid(), "pack": str(case["pack"]),
                                     "review_id": "recovery-test", "point": 1})
    case["lock"].write_bytes(case["lock"].read_bytes() + b" ")
    with pytest.raises(ValueError, match="generation|changed"):
        ledger.release_send_guard(case["home"], str(case["lock"]), guard["guard_id"])
    assert case["lock"].exists()


def fresh_registered_guard(c):
    c["lock"].unlink()
    c["attempt"].unlink()
    c["home"] = str(c["project"].parent.parent / "new-guard-ledger")
    guard = ledger.create_send_guard(c["home"], c["scope"], str(c["lock"]),
                                    {"pid": os.getpid(), "pack": str(c["pack"]),
                                     "review_id": "recovery-test", "point": 1})
    records, coverage = ledger.scan_project(str(c["project"]), c["scope"]["project_id"],
                                           str(c["reviews"]))
    rows = ledger.append_entries(c["home"], [*records, coverage])
    c["target"] = next(row["entry_id"] for row in rows if row["kind"] == "lock")
    return guard


def test_normal_release_after_unlink_crash_does_not_leave_unknown_lock(case):
    guard = fresh_registered_guard(case)
    append = ledger._append_locked

    def crash(home, path, values):
        if any(row.get("resolution_type") == "send_guard_released" for row in values):
            raise OSError("crash after ordinary release unlink")
        return append(home, path, values)

    with mock.patch.object(ledger, "_append_locked", side_effect=crash), pytest.raises(OSError):
        ledger.release_send_guard(case["home"], str(case["lock"]), guard["guard_id"])
    assert not case["lock"].exists()
    assert summary(case)["unknown_locks"] == []
    prepared = next(row for row in ledger.read_entries(case["home"])
                    if row.get("resolution_type") == "send_guard_release_prepared")
    Path(prepared["archive"]).write_bytes(b"invalid")
    assert summary(case)["unknown_locks"]


def test_normal_release_preparation_does_not_hide_existing_guard(case):
    guard = fresh_registered_guard(case)
    unlink = os.unlink

    def fail_on_guard(path):
        if os.path.abspath(str(path)) == os.path.abspath(str(case["lock"])):
            raise OSError("before unlink")
        return unlink(path)

    # Patch only the guard removal; the POSIX ledger mutex is unlinked on release too.
    with mock.patch.object(ledger.os, "unlink", side_effect=fail_on_guard), pytest.raises(OSError):
        ledger.release_send_guard(case["home"], str(case["lock"]), guard["guard_id"])
    assert case["lock"].exists()
    with mock.patch.dict(ledger.OWN_SEND_GUARDS, {}, clear=True):
        assert summary(case)["unknown_locks"]


def test_pre_intent_guard_can_use_independently_verified_local_not_sent(case):
    guard = fresh_registered_guard(case)
    with mock.patch.object(ledger, "_pid_started_at", return_value=None):
        value = assess(case)
        confirmation = {
            "source_kind": "local_pre_request", "terminal_state": "not_sent",
            "intent_id": None, "guard_id": guard["guard_id"],
            "no_valid_review": True, "request_identity": guard["guard_id"],
            "checked_at": ledger.utc_now(), "implementation": [],
        }
        for role, source in (("dispatcher", ROOT / "scripts" / "audit_dispatch.py"),
                             ("ledger", ROOT / "scripts" / "review_ledger.py")):
            snapshot = case["project"] / (role + "-before-request.py")
            snapshot.write_bytes(source.read_bytes())
            confirmation["implementation"].append(
                {"role": role, "path": str(snapshot), "sha256": digest(snapshot)})
        trace = case["project"] / "pre-request-trace.txt"
        trace.write_text("Independent fixture: guard created; no intent claim or request occurred.",
                         encoding="utf-8")
        confirmation["record"] = {"path": str(trace), "sha256": digest(trace)}
        assess(case, upstream_confirmation=confirmation)
        recover(case)
        assert ledger.blocking_reason(summary(case)) is None
        # The retained source snapshot is evidence, not a mutable installed path.
        Path(confirmation["implementation"][0]["path"]).write_bytes(b"different implementation")
        assert ledger.blocking_reason(summary(case))[0] == "review_history"


def test_one_confirmation_cannot_resolve_two_distinct_attempts(case):
    rows = [row for row in ledger.read_entries(case["home"]) if row["kind"] != "attempt"]
    for index, upstream in ((1, {"response_id": "known-response"}), (2, {})):
        payload = dict(case["data"], failure_kind="transport", status=None, upstream=upstream,
                       created_at=(case["moment"] + datetime.timedelta(seconds=index + 2)).isoformat())
        path = Path(str(case["pack"]) + ".attempt-mixed-%s.json" % index)
        path.write_text(json.dumps(payload), encoding="utf-8")
        rows.append(ledger.attempt_entry(case["scope"], payload["binding"], str(path), "transport", 1))
        if index == 2:
            unknown_sha = digest(path)
    case["attempt"].unlink()
    case["home"] = str(case["project"].parent.parent / "mixed-attempt-ledger")
    ledger.append_entries(case["home"], rows)
    draft = assess(case)
    terminal = case["project"] / "only-known-terminal.json"
    terminal.write_text('{"request":"known-response","terminal":"failed"}', encoding="utf-8")
    assess(case, upstream_confirmations=[{
        "terminal_state": "failed", "intent_id": draft["target"]["intent_id"],
        "no_valid_review": True, "request_identity": "known-response",
        "attempt_sha256": unknown_sha, "source_kind": "gateway", "checked_at": ledger.utc_now(),
        "record": {"path": str(terminal), "sha256": digest(terminal)}}])
    with pytest.raises(ValueError, match="identity|request|terminal"):
        recover(case, dry_run=True)
