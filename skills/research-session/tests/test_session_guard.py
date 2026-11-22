"""In-session guard: approval binding, budgets and fail-closed state transitions."""

import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "session_guard.py"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def plan_hash(plan):
    return hashlib.sha256(canonical({key: value for key, value in plan.items()
                                     if key != "plan_sha256"}).encode("utf-8")).hexdigest()


def default_actions():
    return [
        {"id": "inspect", "kind": "inspect", "description": "Read the bound evidence",
         "seconds": 60, "requires": [], "phase": "required"},
        {"id": "verify", "kind": "test", "description": "Run the local check",
         "seconds": 60, "requires": ["inspect"], "phase": "required"},
        {"id": "draft", "kind": "draft", "description": "Write the short delta",
         "seconds": 30, "requires": ["verify"], "phase": "extension"},
    ]


def build(tmp_path, actions=None, deadline_seconds=3600, expires_seconds=1800):
    tmp_path.mkdir(parents=True, exist_ok=True)
    moment = dt.datetime.now(dt.timezone.utc)
    source = tmp_path / "approval-source.md"
    source.write_text("Approved: run the fixture plan inside this session.\n", encoding="utf-8")
    plan = {
        "schema": "research-session/session-plan/v1",
        "task_id": "fixture-task",
        "project_id": "fixture/project-991",
        "actions": actions if actions is not None else default_actions(),
        "deadline": (moment + dt.timedelta(seconds=deadline_seconds)).isoformat(),
        "max_requests": 12,
        "reserved_requests": 2,
        "closeout_seconds": 60,
    }
    plan["plan_sha256"] = plan_hash(plan)
    approval = {
        "schema": "research-session/session-approval/v1",
        "task_id": "fixture-task",
        "plan_sha256": plan["plan_sha256"],
        "approved_at": (moment - dt.timedelta(minutes=5)).isoformat(),
        "expires_at": (moment + dt.timedelta(seconds=expires_seconds)).isoformat(),
        "source": {"path": str(source),
                   "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "revoked": False,
    }
    plan_path = tmp_path / "plan.json"
    approval_path = tmp_path / "approval.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    approval_path.write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
    return {"plan": plan_path, "approval": approval_path, "source": source,
            "state": tmp_path / "state.json", "plan_data": plan, "approval_data": approval}


def rewrite(record, mutate_plan=None, rehash=True, mutate_approval=None):
    plan = json.loads(record["plan"].read_text(encoding="utf-8"))
    if mutate_plan is not None:
        mutate_plan(plan)
    if rehash:
        plan["plan_sha256"] = plan_hash(plan)
    record["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    approval = json.loads(record["approval"].read_text(encoding="utf-8"))
    approval["plan_sha256"] = plan["plan_sha256"]
    if mutate_approval is not None:
        mutate_approval(approval)
    record["approval"].write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
    record["plan_data"], record["approval_data"] = plan, approval


def invoke(*arguments):
    assert SCRIPT.is_file(), "session guard is not implemented"
    process = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), *map(str, arguments)],
                             capture_output=True, encoding="utf-8", timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.stdout.strip().startswith("{"), process.stderr
    return process.returncode, json.loads(process.stdout)


def start(record, *extra):
    return invoke("start", "--plan", record["plan"], "--approval", record["approval"],
                  "--state", record["state"], *extra)


def test_valid_approval_runs_every_action_in_order(tmp_path):
    record = build(tmp_path)
    code, output = start(record)
    assert code == 0, output
    assert output["status"] == "started"
    assert output["execution_enabled"] is False

    for action, evidence in (("inspect", "Read README and protocol."),
                             ("verify", "Local check passed with exit 0."),
                             ("draft", "Delta written to the candidate folder.")):
        code, step = invoke("next", "--state", record["state"])
        assert code == 0, step
        assert step["status"] == "ready"
        assert step["action"]["id"] == action
        code, claimed = invoke("claim", "--state", record["state"], "--action", action)
        assert code == 0, claimed
        assert claimed["action"]["status"] == "in_flight"
        code, done = invoke("complete", "--state", record["state"], "--action", action,
                            "--evidence", evidence)
        assert code == 0, done
        assert done["action"]["status"] == "completed"

    code, stop = invoke("next", "--state", record["state"])
    assert code == 0, stop
    assert stop["status"] == "stop"
    assert stop["reason"] == "all_actions_complete"
    code, status = invoke("status", "--state", record["state"])
    assert code == 0
    assert status["counts"]["completed"] == 3
    assert status["execution_enabled"] is False


@pytest.mark.parametrize("mutation,expected", [
    (lambda plan: plan["actions"].append(dict(plan["actions"][0])), "duplicate action id"),
    (lambda plan: plan["actions"][0].update(requires=["verify"]), "earlier action"),
    (lambda plan: plan["actions"][0].update(phase="optional"), "required or extension"),
    (lambda plan: plan["actions"][0].update(seconds=-1), "integer >= 0"),
    (lambda plan: plan.update(closeout_seconds=-1), "integer >= 0"),
    (lambda plan: plan.update(max_requests=2, reserved_requests=2), "must exceed"),
    (lambda plan: plan["actions"][1].update(requires=["inspect", "inspect"]), "repeats"),
])
def test_invalid_plan_is_rejected(tmp_path, mutation, expected):
    record = build(tmp_path)
    rewrite(record, mutate_plan=mutation)
    code, output = start(record)
    assert code == 2, output
    assert expected in output["error"]
    assert output["status"] == "rejected"


@pytest.mark.parametrize("mutation,expected", [
    (lambda approval: approval.update(revoked=True), "revoked"),
    (lambda approval: approval.update(revoked="false"), "revoked"),
    (lambda approval: approval.update(task_id="other-task"), "task_id"),
    (lambda approval: approval.update(plan_sha256="0" * 64), "plan hash"),
    (lambda approval: approval.update(approved_at="2099-01-02T00:00:00+00:00",
                                      expires_at="2099-01-01T00:00:00+00:00"), "after"),
])
def test_invalid_approval_is_rejected(tmp_path, mutation, expected):
    record = build(tmp_path)
    rewrite(record, mutate_approval=mutation)
    code, output = start(record)
    assert code == 2, output
    assert expected in output["error"]


def test_approval_may_carry_thread_and_host_verification_fields(tmp_path):
    record = build(tmp_path)
    rewrite(record, mutate_approval=lambda approval: approval.update(
        threadId="thread-123", verifiedByHost=True))
    code, output = start(record)
    assert code == 0, output
    assert output["status"] == "started"


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_verified_by_host_must_be_boolean_true(tmp_path, value):
    record = build(tmp_path)
    rewrite(record, mutate_approval=lambda approval: approval.update(verifiedByHost=value))
    code, output = start(record)
    assert code == 2, output
    assert "verifiedByHost" in output["error"]


@pytest.mark.parametrize("value", ["", "   ", 7, None])
def test_thread_id_must_be_nonempty_text(tmp_path, value):
    record = build(tmp_path)
    rewrite(record, mutate_approval=lambda approval: approval.update(threadId=value))
    code, output = start(record)
    assert code == 2, output
    assert "threadId" in output["error"]


def test_other_unknown_approval_fields_are_still_rejected(tmp_path):
    record = build(tmp_path)
    rewrite(record, mutate_approval=lambda approval: approval.update(extraField=True))
    code, output = start(record)
    assert code == 2, output
    assert "unknown fields" in output["error"]


def test_missing_source_swapped_source_and_expired_approval_are_rejected(tmp_path):
    record = build(tmp_path)
    record["source"].unlink()
    code, output = start(record)
    assert code == 2
    assert "source" in output["error"]

    record = build(tmp_path / "sha")
    record["source"].write_text("Changed approval record.\n", encoding="utf-8")
    code, output = start(record)
    assert code == 2
    assert "sha256" in output["error"]

    record = build(tmp_path / "expired", expires_seconds=-60)
    code, output = start(record)
    assert code == 2
    assert "expired" in output["error"]


def test_plan_hash_drift_before_and_after_start_is_rejected(tmp_path):
    record = build(tmp_path)
    plan = json.loads(record["plan"].read_text(encoding="utf-8"))
    plan["actions"][0]["description"] = "Silently widened scope"
    record["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    code, output = start(record)
    assert code == 2
    assert "plan hash drift" in output["error"]

    record = build(tmp_path / "after")
    assert start(record)[0] == 0
    plan = json.loads(record["plan"].read_text(encoding="utf-8"))
    plan["actions"][0]["description"] = "Silently widened after start"
    record["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    code, output = invoke("next", "--state", record["state"])
    assert code == 2
    assert "plan hash drift" in output["error"]


def test_deadline_and_closeout_must_cover_required_work(tmp_path):
    record = build(tmp_path, deadline_seconds=-1)
    code, output = start(record)
    assert code == 2
    assert "deadline" in output["error"]

    record = build(tmp_path / "short", deadline_seconds=60)
    code, output = start(record)
    assert code == 2
    assert "insufficient time budget" in output["error"]


def test_expired_session_and_revoked_approval_stop_in_flight_progress(tmp_path):
    record = build(tmp_path)
    assert start(record)[0] == 0
    state = json.loads(record["state"].read_text(encoding="utf-8"))
    state["deadline"] = "2000-01-01T00:00:00+00:00"
    record["state"].write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    code, output = invoke("next", "--state", record["state"])
    assert code == 2
    assert "deadline exceeded" in output["error"]

    record = build(tmp_path / "revoked")
    assert start(record)[0] == 0
    approval = json.loads(record["approval"].read_text(encoding="utf-8"))
    approval["revoked"] = True
    record["approval"].write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")
    code, output = invoke("next", "--state", record["state"])
    assert code == 2
    assert "revoked" in output["error"]


def test_claim_requires_ready_known_and_unattempted_action(tmp_path):
    record = build(tmp_path)
    assert start(record)[0] == 0

    code, output = invoke("claim", "--state", record["state"], "--action", "missing")
    assert code == 2
    assert "not in the plan" in output["error"]

    code, output = invoke("claim", "--state", record["state"], "--action", "verify")
    assert code == 2
    assert "dependencies" in output["error"]

    assert invoke("claim", "--state", record["state"], "--action", "inspect")[0] == 0
    code, output = invoke("claim", "--state", record["state"], "--action", "inspect")
    assert code == 2
    assert "already claimed" in output["error"]


def test_complete_requires_claim_and_nonempty_evidence(tmp_path):
    record = build(tmp_path)
    assert start(record)[0] == 0
    code, output = invoke("complete", "--state", record["state"], "--action", "inspect",
                          "--evidence", "premature")
    assert code == 2
    assert "claimed" in output["error"]

    assert invoke("claim", "--state", record["state"], "--action", "inspect")[0] == 0
    code, output = invoke("complete", "--state", record["state"], "--action", "inspect",
                          "--evidence", "   ")
    assert code == 2
    assert "nonempty" in output["error"]


def test_failed_action_marks_unknown_outcome_and_stops_the_session(tmp_path):
    record = build(tmp_path)
    assert start(record)[0] == 0
    assert invoke("claim", "--state", record["state"], "--action", "inspect")[0] == 0
    code, output = invoke("fail", "--state", record["state"], "--action", "inspect",
                          "--reason", "watchdog expired with no result")
    assert code == 0, output
    assert output["status"] == "stop"
    assert "action_failed" in output["reason"]

    code, output = invoke("next", "--state", record["state"])
    assert code == 2
    assert "not running" in output["error"]
    code, status = invoke("status", "--state", record["state"])
    assert code == 0
    assert status["state"] == "stopped"
    assert status["counts"]["failed"] == 1


def test_pause_and_status_work_without_any_background_runtime(tmp_path):
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "Popen", "multiprocessing", "threading", "daemon"):
        assert forbidden not in source

    record = build(tmp_path)
    assert start(record)[0] == 0
    code, output = invoke("pause", "--state", record["state"], "--reason", "user left")
    assert code == 0, output
    assert output["state"] == "paused"
    code, output = invoke("next", "--state", record["state"])
    assert code == 2
    assert "paused" in output["error"]
    code, output = invoke("status", "--state", record["state"])
    assert code == 0
    assert output["state"] == "paused"
    assert output["pause_reason"] == "user left"


def test_start_never_resets_an_existing_state_file(tmp_path):
    record = build(tmp_path)
    assert start(record)[0] == 0
    code, output = start(record)
    assert code == 2
    assert "already exists" in output["error"]
