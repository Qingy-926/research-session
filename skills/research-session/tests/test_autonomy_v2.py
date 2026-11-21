import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FUTURE = "2099-01-01T00:00:00Z"


def make_action(action_id, kind="inspect", *, paid=False, phase="required", requires=None, cost=0):
    return {
        "id": action_id, "kind": kind, "description": "合成动作",
        "context_paths": [], "phase": phase, "requires": requires or [],
        "condition": None if phase == "required" else {"action": requires[0], "result": "succeeded"},
        "continue_on_review_failure": False, "review": None, "paid": paid,
        "estimate": {
            "duration": {"min_seconds": 30, "max_seconds": 60, "basis": "conservative", "evidence_path": None, "evidence_sha256": None},
            "gpu": {"status": "not_applicable", "device": None, "count": 0, "min_seconds": 0, "max_seconds": 0, "basis": "not needed", "evidence_path": None, "evidence_sha256": None},
            "model_calls": int(paid), "input_tokens": 1000 if paid else None,
            "output_tokens": 500 if paid else None, "cost_cny_fen": cost,
            "cost_basis": "synthetic estimate", "disk_bytes": 0,
        },
        "watchdog_seconds": 180, "operation": None, "outputs": [],
    }


@pytest.fixture
def task(tmp_path):
    project = tmp_path / "project-999-fixture"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("Synthetic only; no model or GPU calls.\n", encoding="utf-8")
    evidence = project / "evidence.json"
    evidence.write_text('{"observed": 2}\n', encoding="utf-8")
    action = make_action("inspect")
    action["context_paths"] = [str(readme), str(evidence)]
    extension = make_action("draft", "draft", paid=True, phase="extension", requires=["inspect"], cost=800)
    extension["context_paths"] = [str(readme), str(evidence)]
    card = {
        "schema": "research-session/start-card/v2", "project_root": str(project),
        "run_id": "run-001", "revision": 1, "previous_card_sha256": None,
        "goal": "核验合成结果", "expires_at_utc": FUTURE,
        "context": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (readme, evidence)],
        "budgets": {"max_action_seconds": 1000, "gpu_seconds": 1200, "disk_bytes": 1024,
                    "cost_soft_limit_cny_fen": 1000, "currency": "CNY"},
        "prohibited": ["真实模型调用", "GPU执行", "改协议"],
        "stop_conditions": ["输入漂移", "未批准取舍"], "recommended_option": "verify",
        "options": [{"id": "verify", "deliverables": ["核验清单"], "reason": "合成验收",
                     "write_scope": ["runs/run-001"], "new_permissions": [],
                     "state_path": "runs/run-001/state.sqlite3", "actions": [action, extension]}],
    }
    path = tmp_path / "card.json"
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return path, card, project


def invoke(script, *args):
    assert script.is_file(), f"missing implementation: {script}"
    if args[:1] == ("start",):
        args = ("start", "--fixture", *args[1:])
    proc = subprocess.run([sys.executable, "-B", str(script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=15,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.returncode, json.loads(proc.stdout)


def confirmation(path, card):
    record = {"schema": "research-session/start-selection/v2",
              "card_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "option_id": "verify", "expires_at_utc": card["expires_at_utc"], "revoked": False}
    target = path.parent / ("confirmation-%d.json" % card["revision"])
    target.write_text(json.dumps(record), encoding="utf-8")
    return target


def test_v2_has_no_task_wall_clock_and_cost_is_soft(task):
    path, card, _ = task
    card["options"][0]["actions"][1]["estimate"]["cost_cny_fen"] = 1400
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    code, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path, "--choice", "verify")
    assert code == 2 and result["state"] == "awaiting_confirmation"
    option = result["options"][0]
    assert option["fits_budget"] is True and option["cost_soft_warning"] is True
    assert "estimated_runtime_seconds" not in option
    assert result["execution_enabled"] is False


def test_confirmation_prepares_but_does_not_authenticate_or_execute(task):
    path, card, _ = task
    record = confirmation(path, card)
    code, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path,
                         "--choice", "verify", "--confirmation", record)
    assert code == 0 and result["state"] == "preparation_complete"
    assert result["controller_available"] is True
    assert result["authorization_verified"] is False
    assert result["execution_enabled"] is False


def test_gpu_estimate_can_be_explicitly_unknown(task):
    path, card, _ = task
    action = card["options"][0]["actions"][1]
    action["estimate"]["gpu"] = {
        "status": "unknown", "device": None, "count": None,
        "min_seconds": None, "max_seconds": None, "basis": "No comparable run.",
        "evidence_path": None, "evidence_sha256": None,
    }
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    _, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path)
    option = result["options"][0]
    assert option["fits_budget"] is True
    assert "gpu_duration:draft" in option["unknown_estimates"]
    assert option["actions"][1]["gpu_estimate"]["status"] == "unknown"


@pytest.mark.parametrize("watchdog", [60, 179])
def test_conservative_watchdog_requires_three_times_upper_estimate(task, watchdog):
    path, card, _ = task
    card["options"][0]["actions"][0]["watchdog_seconds"] = watchdog
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    code, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path)
    assert code == 2 and result["state"] == "invalid_card"
    assert "watchdog" in result["error"]


def test_arbitrary_command_is_rejected(task):
    path, card, _ = task
    assert invoke(ROOT / "scripts/autonomy_start.py", "--card", path)[1]["state"] == "awaiting_choice"
    card["options"][0]["actions"][0]["command"] = "python anything.py"
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    code, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path)
    assert code == 2 and result["state"] == "invalid_card" and "action" in result["error"]


def test_runtime_persists_claim_completion_and_evidence(task):
    path, card, project = task
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    code, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    assert code == 0
    state = Path(started["state_path"])
    code, claimed = invoke(runtime, "claim", "--state", state, "--action", "inspect")
    assert code == 0 and claimed["action"]["watchdog_seconds"] == 180
    assert "command" not in claimed["action"]
    code, finished = invoke(runtime, "complete", "--state", state, "--action", "inspect",
                            "--outcome", "succeeded", "--evidence", project / "evidence.json")
    assert code == 0 and finished["action_state"] == "SUCCEEDED"
    _, status = invoke(runtime, "status", "--state", state)
    assert status["run"]["state"] == "RUNNING"
    assert status["actions"][0]["state"] == "SUCCEEDED"


def test_extension_cannot_start_before_dependency(task):
    path, card, _ = task
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    _, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    code, result = invoke(runtime, "claim", "--state", started["state_path"],
                          "--action", "draft")
    assert code == 3 and result["state"] == "BLOCKED"


def test_recovery_marks_inflight_action_uncertain_without_replay(task):
    path, card, _ = task
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    _, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    state = started["state_path"]
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 0
    code, recovered = invoke(runtime, "resume", "--state", state, "--card", path,
                             "--choice", "verify", "--confirmation", record)
    assert code == 3
    assert recovered["run"]["state"] == "PAUSED"
    assert recovered["actions"][0]["state"] == "UNCERTAIN"
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 3


def test_soft_cost_gate_counts_failed_call_after_it_finishes(task):
    path, card, project = task
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    _, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    state = started["state_path"]
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 0
    assert invoke(runtime, "complete", "--state", state, "--action", "inspect", "--outcome", "succeeded",
                  "--evidence", project / "README.md")[0] == 0
    assert invoke(runtime, "claim", "--state", state, "--action", "draft")[0] == 0
    code, result = invoke(runtime, "complete", "--state", state, "--action", "draft", "--outcome", "failed",
                          "--evidence", project / "evidence.json", "--cost-cny-fen", "1200",
                          "--cost-source", "token_rate_estimate")
    assert code == 0 and result["run"]["state"] == "PAUSED_COST"
    assert result["cost"]["known_cny_fen"] == 1200
    assert result["cost"]["threshold_cny_fen"] == 1000
    assert invoke(runtime, "claim", "--state", state, "--action", "draft")[0] == 3


def test_fixed_local_executes_with_write_limit(task):
    path, card, project = task
    action = card["options"][0]["actions"][0]
    action["estimate"]["disk_bytes"] = 100
    action["operation"] = {"adapter": "hash", "source": str(project / "README.md"),
                           "destination": "runs/run-001/readme-hash.json"}
    action["outputs"] = [action["operation"]["destination"]]
    path.write_text(json.dumps(card), encoding="utf-8")
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    code, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    assert code == 0, started
    state = started["state_path"]
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 3
    code, result = invoke(runtime, "execute", "--state", state, "--action", "inspect")
    assert code == 0, result
    output = project / action["operation"]["destination"]
    assert json.loads(output.read_text())["sha256"] == card["context"][0]["sha256"]
    assert invoke(runtime, "execute", "--state", state, "--action", "inspect")[0] == 3


def test_cost_pause_allows_a_free_registered_action(task):
    path, card, project = task
    draft = card["options"][0]["actions"][1]
    draft["estimate"]["cost_cny_fen"] = 1200
    action = make_action("verify-free", "test", phase="extension", requires=["inspect"])
    action["context_paths"] = [str(project / "README.md")]
    action["estimate"]["disk_bytes"] = 100
    destination = "runs/run-001/free-hash.json"
    action["operation"] = {"adapter": "hash", "source": str(project / "README.md"), "destination": destination}
    action["outputs"] = [destination]
    card["options"][0]["actions"].append(action)
    path.write_text(json.dumps(card), encoding="utf-8")
    runtime = ROOT / "scripts/autonomy_run.py"
    record = confirmation(path, card)
    code, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    assert code == 0, started
    state = started["state_path"]
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 0
    assert invoke(runtime, "complete", "--state", state, "--action", "inspect", "--outcome", "succeeded",
                  "--evidence", project / "README.md")[0] == 0
    assert invoke(runtime, "claim", "--state", state, "--action", "draft")[1]["run"]["state"] == "PAUSED_COST"
    code, result = invoke(runtime, "execute", "--state", state, "--action", "verify-free")
    assert code == 0 and result["run"]["state"] == "PAUSED_COST"
    assert (project / destination).is_file()


def test_finish_revalidates_every_required_output(task):
    path, card, project = task
    action = card["options"][0]["actions"][0]
    action["outputs"] = ["runs/run-001/first.json", "runs/run-001/second.json"]
    action["estimate"]["disk_bytes"] = 100
    path.write_text(json.dumps(card), encoding="utf-8")
    runtime = ROOT / "scripts/autonomy_run.py"
    record = confirmation(path, card)
    code, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    assert code == 0, started
    state = started["state_path"]
    first, second = (project / item for item in action["outputs"])
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 0
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    assert invoke(runtime, "complete", "--state", state, "--action", "inspect", "--outcome", "succeeded",
                  "--evidence", first)[0] == 0
    second.write_text('{"changed":true}', encoding="utf-8")
    code, result = invoke(runtime, "finish", "--state", state, "--reason", "drift")
    assert code == 3 and "predecessor output changed" in result["error"]


def test_offline_artifacts_do_not_enter_ordinary_ledger(task, monkeypatch, tmp_path):
    _, _, project = task
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import review_ledger as ledger

    home = tmp_path / "ordinary-ledger"
    monkeypatch.setenv(ledger.LEDGER_ENV, str(home))
    canonical = project / "01-资料/reviews"
    canonical.mkdir(parents=True)
    scope = {"project_root": str(project), "canonical": str(canonical), "project_id": ledger.project_identity(str(project))}
    proc_code, result = invoke(ROOT / "scripts/audit_dispatch.py", "--ledger-backfill", project)
    assert proc_code != 0 and result["gate_passed"] is False
    assert not home.exists()
    assert ledger.find_project_roots(str(tmp_path)) == []
    assert ledger.find_project_roots(str(canonical)) == []
    pack = canonical / "fixture.md"
    pack.write_text("Synthetic pack\n", encoding="utf-8")
    source = project / "evidence.json"
    inputs = canonical / "inputs.json"
    inputs.write_text(json.dumps({"review_id": "fixture-review", "files": [{"path": str(source),
                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}), encoding="utf-8")
    code, result = invoke(ROOT / "scripts/audit_dispatch.py", "--pack", pack, "--point", 1, "--inputs", inputs, "--dry-run")
    assert code != 0 and result["stage"] == "review_scope" and not home.exists(), result
    with pytest.raises(ValueError, match="dedicated test ledger"):
        ledger.preflight(None, scope, "fixture-review", 1)
    assert not home.exists()
    ordinary = tmp_path / "project-998-ordinary"
    ordinary_reviews = ordinary / "01-资料/reviews"
    ordinary_reviews.mkdir(parents=True)
    nested = ordinary / project.name
    nested.mkdir()
    (nested / "bad.review.json").write_text("malformed", encoding="utf-8")
    payload = {"unattended": {"run_id": "run-001"}, "binding": {"review_id": "fixture-review", "point": 1}}
    for name in ("copy.review.json", "copy.attempt-test.json", ".review-send-copy.lock"):
        (ordinary_reviews / name).write_text(json.dumps(payload), encoding="utf-8")
    ledger.backfill(str(ordinary), None)
    assert {item["kind"] for item in ledger.read_entries()} == {"coverage"}


def test_modified_source_blocks_before_dispatch(task):
    path, card, project = task
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    _, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    (project / "evidence.json").write_text("changed")
    code, result = invoke(runtime, "claim", "--state", started["state_path"], "--action", "inspect")
    assert code == 3 and "drift" in result["error"]


def test_confirmed_cost_revision_preserves_history(task):
    path, card, project = task
    runtime = ROOT / "scripts/autonomy_run.py"
    card["options"][0]["actions"][1]["estimate"]["cost_cny_fen"] = 1400
    path.write_text(json.dumps(card), encoding="utf-8")
    record = confirmation(path, card)
    _, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    state = started["state_path"]
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 0
    assert invoke(runtime, "complete", "--state", state, "--action", "inspect", "--outcome", "succeeded",
                  "--evidence", project / "evidence.json")[0] == 0
    assert invoke(runtime, "claim", "--state", state, "--action", "draft")[1]["run"]["state"] == "PAUSED_COST"
    assert invoke(runtime, "resume", "--state", state, "--card", path, "--choice", "verify", "--confirmation", record)[0] == 3
    card["revision"] = 2
    card["previous_card_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    card["budgets"]["cost_soft_limit_cny_fen"] = 2000
    path.write_text(json.dumps(card), encoding="utf-8")
    assert invoke(runtime, "resume", "--state", state, "--card", path, "--choice", "verify", "--confirmation", record)[0] == 3
    revised = confirmation(path, card)
    code, result = invoke(runtime, "resume", "--state", state, "--card", path, "--choice", "verify", "--confirmation", revised)
    assert code == 0 and result["actions"][0]["attempt"] == 1
    assert invoke(runtime, "claim", "--state", state, "--action", "draft")[0] == 0


def build_reviews(card, project, rounds):
    path = project / "01-资料/reviews"
    path.mkdir(parents=True)
    review_actions = []
    for number in range(1, rounds + 1):
        previous = f"r{number - 1}" if number > 1 else None
        verification = f"verify{number - 1}" if previous else None
        if verification:
            candidate = make_action(f"repair{number - 1}", "candidate_patch", phase="extension", requires=[previous])
            candidate["condition"]["result"] = "review_not_passed"
            candidate["continue_on_review_failure"] = True
            candidate["context_paths"] = [str(project / "evidence.json")]
            candidate["outputs"] = [f"runs/run-001/{candidate['id']}.json"]
            candidate["estimate"]["disk_bytes"] = 100
            review_actions.append(candidate)
            check = make_action(verification, "test", phase="extension", requires=[previous, candidate["id"]])
            check["condition"]["result"] = "review_not_passed"
            check["continue_on_review_failure"] = True
            check["context_paths"] = [str(project / "evidence.json")]
            check["outputs"] = [f"runs/run-001/{verification}.json"]
            check["estimate"]["disk_bytes"] = 512
            check["operation"] = {"adapter": "json-match", "source": candidate["outputs"][0],
                                  "destination": check["outputs"][0], "expected": str(project / "evidence.json"),
                                  "finding_ids": ["E-1"]}
            review_actions.append(check)
        prepare_id = f"prepare{number}"
        prepare = make_action(prepare_id, "draft", phase="extension" if previous else "required",
                              requires=[verification] if previous else [])
        prepare["context_paths"] = [str(project / "evidence.json")]
        prepare["estimate"]["disk_bytes"] = 2000
        review_actions.append(prepare)
        action = make_action(f"r{number}", "independent_review", paid=True, cost=150,
                             phase="extension" if previous else "required", requires=[previous, verification, prepare_id] if previous else [prepare_id])
        action["context_paths"] = [str(project / "evidence.json")]
        action["continue_on_review_failure"] = bool(previous)
        if previous:
            action["condition"]["result"] = "review_not_passed"
        pack = path / f"pack-{number}.md"
        inputs = path / f"inputs-{number}.json"
        prepare["outputs"] = [pack.relative_to(project).as_posix(), inputs.relative_to(project).as_posix()]
        action["review"] = {"review_id": "fixture-review", "point": 1, "round": number,
                            "pack_path": pack.relative_to(project).as_posix(), "inputs_path": inputs.relative_to(project).as_posix(),
                            "previous_action": previous, "verification_action": verification, "prepare_action": prepare_id,
                            "transport": "responses", "max_output_tokens": 2000}
        review_actions.append(action)
    option = card["options"][0]
    option["write_scope"].append("01-资料/reviews")
    option["actions"] = review_actions
    card["budgets"]["disk_bytes"] = 200000
    return [action for action in review_actions if action["review"]]


def test_fourth_round_rejected_even_with_new_action_id(task):
    path, card, project = task
    build_reviews(card, project, 4)
    path.write_text(json.dumps(card), encoding="utf-8")
    code, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path)
    assert code == 2 and "cannot exceed three" in result["error"]


@pytest.mark.parametrize("mode", ["three_nonpasses", "pass_then_drift", "transport_failure", "revoked_while_sending"])
def test_real_dispatch_code_with_offline_transport(task, monkeypatch, mode):
    path, card, project = task
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import audit_dispatch as dispatch
    import autonomy_run as runtime
    calls = []
    blocker = {"id": "E-1", "kind": "blocking", "domain": "evidence", "location": "fixture", "evidence": "fixture",
               "impact": "missing evidence", "change": "supply evidence", "acceptance": "verify receipt"}

    def fake_transport(model, reasoning_effort):
        calls.append((model, reasoning_effort))
        if mode == "transport_failure":
            return None, "offline simulated transport failure"
        if mode == "revoked_while_sending":
            selection = json.loads(record.read_bytes())
            selection["revoked"] = True
            record.write_text(json.dumps(selection), encoding="utf-8")
        decision = "通过" if mode == "pass_then_drift" else ("不通过" if len(calls) in (1, 3) else "未完成")
        report = {"decision": decision, "summary": "Offline only." * (4000 if mode == "pass_then_drift" else 1),
                  "checklist": [{"item": index, "status": "通过", "reason": ""} for index in range(1, 8)],
                  "findings": [blocker] if decision != "通过" else [],
                  "closed_findings": [{"id": "E-1", "status": "未解决", "evidence": "still missing evidence"}] if len(calls) > 1 else []}
        return 200, json.dumps({"status": "completed", "output_text": json.dumps(report), "usage": {"billed_cny_fen": 100}})

    real_subprocess = subprocess.run

    def offline_subprocess(command, **kwargs):
        import contextlib
        import io
        from types import SimpleNamespace
        if not command[3].endswith("audit_dispatch.py"):
            return real_subprocess(command, **kwargs)
        monkeypatch.setattr(sys, "argv", [command[3], *command[4:]])
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), pytest.raises(SystemExit) as exit_result:
            dispatch.main(offline_transport=fake_transport)
        assert json.loads(stream.getvalue()).get("stage") != "send_guard", stream.getvalue()
        return SimpleNamespace(stdout=stream.getvalue().encode(), returncode=exit_result.value.code)

    monkeypatch.setattr(dispatch, "post_with_retry", lambda *args, **kwargs: pytest.fail("no real transport"))
    monkeypatch.setattr(dispatch, "http_post_responses", lambda *args, **kwargs: pytest.fail("no real transport"))
    monkeypatch.setattr(dispatch, "responses_post", lambda *args: pytest.fail("no real transport"))
    monkeypatch.setattr(dispatch, "read_current_provider", lambda: pytest.fail("no user provider read"))
    monkeypatch.setattr(runtime.subprocess, "run", offline_subprocess)
    review_actions = build_reviews(card, project, 3)
    repair = make_action("local-repair", "candidate_patch", phase="extension", requires=["r3"])
    repair["condition"]["result"] = "review_not_passed"
    repair["continue_on_review_failure"] = True
    repair["context_paths"] = review_actions[0]["context_paths"]
    repair["outputs"] = ["runs/run-001/repair.json"]
    repair["estimate"]["disk_bytes"] = 100
    verify_final = make_action("verify-final", "test", phase="extension", requires=["r3", "local-repair"])
    verify_final["condition"]["result"] = "review_not_passed"
    verify_final["continue_on_review_failure"] = True
    verify_final["context_paths"] = repair["context_paths"]
    verify_final["outputs"] = ["runs/run-001/verify-final.json"]
    verify_final["estimate"]["disk_bytes"] = 512
    verify_final["operation"] = {"adapter": "json-match", "source": repair["outputs"][0],
                                 "destination": verify_final["outputs"][0], "expected": str(project / "evidence.json"),
                                 "finding_ids": ["E-1"]}
    handoff = make_action("independent-draft", "draft", phase="extension", requires=["verify-final"])
    handoff["context_paths"] = repair["context_paths"]
    gated = make_action("dependent", "draft", requires=["r1"])
    gated["context_paths"] = repair["context_paths"]
    card["options"][0]["actions"] += [repair, verify_final, handoff, gated]
    path.write_text(json.dumps(card), encoding="utf-8")
    record = confirmation(path, card)

    def cli(*arguments):
        if arguments[:1] == ("start",):
            arguments = ("start", "--fixture", *arguments[1:])
        return runtime.main(list(map(str, arguments)))

    def produce(number):
        action = review_actions[number - 1]["review"]
        files = [card["context"][1]]
        if number > 1:
            verification = project / f"runs/run-001/verify{number - 1}.json"
            action_id = action["verification_action"]
            assert cli("claim", "--state", state, "--action", action_id) == 3
            repair_id = f"repair{number - 1}"
            candidate = project / "runs/run-001" / (repair_id + ".json")
            assert cli("claim", "--state", state, "--action", repair_id) == 0
            candidate.write_bytes((project / "evidence.json").read_bytes())
            assert cli("complete", "--state", state, "--action", repair_id, "--outcome", "succeeded", "--evidence", candidate) == 0
            assert cli("execute", "--state", state, "--action", action_id) == 0
            files.append({"path": str(verification), "sha256": hashlib.sha256(verification.read_bytes()).hexdigest()})
        assert cli("claim", "--state", state, "--action", action["prepare_action"]) == 0
        pack = project / action["pack_path"]
        pack.write_text(f"Synthetic increment {number}\n", encoding="utf-8")
        (project / action["inputs_path"]).write_text(json.dumps({"review_id": "fixture-review", "files": files}), encoding="utf-8")
        assert cli("complete", "--state", state, "--action", action["prepare_action"], "--outcome", "succeeded", "--evidence", pack) == 0

    assert cli("start", "--card", path, "--choice", "verify", "--confirmation", record) == 0
    state = project / card["options"][0]["state_path"]
    produce(1)
    uncertain = mode in {"transport_failure", "revoked_while_sending"}
    assert cli("execute", "--state", state, "--action", "r1") == (3 if uncertain else 0)
    if mode == "three_nonpasses":
        assert cli("execute", "--state", state, "--action", "r2") == 3
        produce(2)
        assert cli("execute", "--state", state, "--action", "r2") == 0
        assert cli("pause", "--state", state, "--reason", "handoff") == 0
        assert cli("resume", "--state", state, "--card", path, "--choice", "verify", "--confirmation", record) == 0
        produce(3)
        assert cli("execute", "--state", state, "--action", "r3") == 0
        assert cli("execute", "--state", state, "--action", "r3") == 3
        assert cli("claim", "--state", state, "--action", "local-repair") == 0
        evidence = project / "runs/run-001/repair.json"
        evidence.write_bytes((project / "evidence.json").read_bytes())
        assert cli("complete", "--state", state, "--action", "local-repair", "--outcome", "succeeded", "--evidence", evidence) == 0
        assert cli("execute", "--state", state, "--action", "verify-final") == 0
        assert cli("claim", "--state", state, "--action", "independent-draft") == 0
        assert cli("complete", "--state", state, "--action", "independent-draft", "--outcome", "succeeded",
                   "--evidence", project / "evidence.json") == 0
    elif mode == "pass_then_drift":
        assert cli("execute", "--state", state, "--action", "r2") == 3
        pack = project / review_actions[0]["review"]["pack_path"]
        assert Path(str(pack) + ".review.json").stat().st_size > 64 * 1024
        pack.write_text("stale receipt")
    else:
        assert not list((project / "01-资料/reviews").glob("*.review.json"))
        assert cli("execute", "--state", state, "--action", "r1") == 3
        assert cli("execute", "--state", state, "--action", "r2") == 3
    assert cli("claim", "--state", state, "--action", "dependent") == 3
    assert cli("finish", "--state", state, "--reason", "not complete") == 3
    count = 3 if mode == "three_nonpasses" else 1
    assert calls == [("offline-unconfigured", "xhigh")] * count
    with sqlite3.connect(state) as connection:
        assert connection.execute("SELECT COUNT(*) FROM actions WHERE dispatch_used=1").fetchone()[0] == count
        assert connection.execute("SELECT SUM(cost_cny_fen) FROM actions").fetchone()[0] == (None if uncertain else 100 * count)


def test_pause_of_claimed_action_commits_then_stops_dispatch(task):
    path, card, project = task
    record = confirmation(path, card)
    runtime = ROOT / "scripts/autonomy_run.py"
    _, started = invoke(runtime, "start", "--card", path, "--choice", "verify", "--confirmation", record)
    state = started["state_path"]
    assert invoke(runtime, "claim", "--state", state, "--action", "inspect")[0] == 0
    _, paused = invoke(runtime, "pause", "--state", state, "--reason", "handoff")
    assert paused["run"]["state"] == "PAUSE_REQUESTED"
    code, finished = invoke(runtime, "complete", "--state", state, "--action", "inspect", "--outcome", "succeeded",
                            "--evidence", project / "README.md")
    assert code == 0 and finished["run"]["state"] == "PAUSED"
    assert invoke(runtime, "claim", "--state", state, "--action", "draft")[0] == 3


def test_review_dispatch_cli_cannot_use_real_transport(task):
    _, _, project = task
    code, result = invoke(ROOT / "scripts/audit_dispatch.py", "--unattended-state", project / "missing.sqlite3",
                          "--unattended-action", "r1", "--pack", project / "missing.md", "--point", 1)
    assert code == 2 and result["stage"] == "unattended"


def test_explicit_fixture_temp_root_is_supported_without_relaxing_project_gate(task, monkeypatch):
    _, _, project = task
    monkeypatch.setenv("RESEARCH_SESSION_FIXTURE_TEMP_ROOT", str(project.parent))
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import autonomy_run as runtime
    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda: str(project.parent / "unrelated-system-temp"))

    assert runtime.fixture_root(project) == project.resolve()
    with pytest.raises(runtime.RunError, match="synthetic project"):
        runtime.fixture_root(project.with_name("project-123-synthetic-probe"))
    with pytest.raises(runtime.RunError, match="synthetic project"):
        runtime.fixture_root(project.parent / "outside" / "project-999-fixture")


def test_local_record_cannot_start_a_real_project(task, monkeypatch):
    path, card, project = task
    actual = project.with_name("project-123-synthetic-probe")
    project.rename(actual)
    card["project_root"] = str(actual)
    for entry in card["context"]:
        entry["path"] = str(actual / Path(entry["path"]).name)
    for action in card["options"][0]["actions"]:
        action["context_paths"] = [str(actual / Path(item).name) for item in action["context_paths"]]
    path.write_text(json.dumps(card), encoding="utf-8")
    record = confirmation(path, card)
    code, result = invoke(ROOT / "scripts/autonomy_run.py", "start", "--card", path, "--choice", "verify", "--confirmation", record)
    assert code == 3 and "synthetic project" in result["error"]
    assert not (actual / card["options"][0]["state_path"]).exists()
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import autonomy_run as runtime

    monkeypatch.setattr(runtime.autonomy_start, "inspect_card_v2",
                        lambda *args, **kwargs: pytest.fail("not allowed to open non-fixture card inputs"))
    with pytest.raises(runtime.RunError, match="synthetic project"):
        runtime.read_card(path)
    outside_state = actual / card["options"][0]["state_path"]
    outside_state.parent.mkdir(parents=True)
    outside_state.touch()
    monkeypatch.setattr(runtime.sqlite3, "connect",
                        lambda *args, **kwargs: pytest.fail("not allowed to open non-fixture SQLite state"))
    with pytest.raises(runtime.RunError, match="synthetic project"):
        runtime.db_connect(outside_state)


def test_review_id_change_does_not_open_another_round_in_same_package(task):
    path, card, project = task
    reviews = build_reviews(card, project, 2)
    reviews[1]["review"]["review_id"] = "another-id"
    path.write_text(json.dumps(card), encoding="utf-8")
    code, result = invoke(ROOT / "scripts/autonomy_start.py", "--card", path)
    assert code == 2 and "cannot change a review object id" in result["error"]
