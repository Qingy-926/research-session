import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/autonomy_start.py"
FUTURE = "2099-01-01T00:00:00Z"


@pytest.fixture
def start_card(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text("Synthetic project: diagnose only; real runner unavailable.\n", encoding="utf-8")
    option = {
        "id": "verify",
        "deliverables": ["核验结论与缺证清单"],
        "reason": "当前缺口是结果可信度，先核验已有证据。",
        "write_scope": [],
        "new_permissions": [],
        "actions": [
            {"id": "inspect", "kind": "inspect", "description": "按README既定口径核验合成结果",
             "context_paths": [str(readme)], "phase": "required", "requires": [], "condition": None,
             "estimate": {"seconds": 600, "model_calls": 0, "gpu_seconds": 0, "disk_bytes": 0, "cost_minor": 0}},
            {"id": "draft", "kind": "draft", "description": "在本会话交付差异与缺证清单",
             "context_paths": [str(readme)], "phase": "extension", "requires": ["inspect"],
             "condition": "已有证据核验结束且输入未漂移",
             "estimate": {"seconds": 300, "model_calls": 0, "gpu_seconds": 0, "disk_bytes": 0, "cost_minor": 0}}
        ]
    }
    card = {
        "schema": "research-session/start-card/v1",
        "project_root": str(project),
        "goal": "核验现有合成结果",
        "expires_at_utc": FUTURE,
        "context": [{"path": str(readme), "sha256": hashlib.sha256(readme.read_bytes()).hexdigest()}],
        "budgets": {"runtime_seconds": 1200, "closeout_seconds": 120, "max_action_seconds": 900, "model_calls": 0,
                    "gpu_seconds": 0, "disk_bytes": 0, "cost_minor": 0, "currency": "CNY", "cost_metered": False},
        "prohibited": ["新实验", "外发", "删除原件", "修改协议"],
        "stop_conditions": ["输入变化", "预算或截止先到", "出现未批准取舍"],
        "recommended_option": "verify",
        "options": [option]
    }
    path = tmp_path / "card.json"
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return path, card


def invoke(path, *arguments):
    assert SCRIPT.is_file(), "conversation startup card checker is not implemented"
    process = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), "--card", str(path), *arguments],
                             capture_output=True, encoding="utf-8", timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert process.stdout.strip().startswith("{"), process.stderr
    return process.returncode, json.loads(process.stdout)


def save(path, card):
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")


def confirmation(path, output, **changes):
    value = {"schema": "research-session/start-selection/v1", "card_sha256": output["card_sha256"],
             "option_id": "verify", "expires_at_utc": FUTURE, "revoked": False}
    value.update(changes)
    target = path.parent / "confirmation.json"
    save(target, value)
    return str(target)


def test_no_reply_never_selects_the_recommended_option(start_card):
    path, _ = start_card
    code, output = invoke(path)
    assert code == 2
    assert output["state"] == "awaiting_choice"
    assert output["selected_option"] is None
    assert output["options"][0]["fits_budget"] is True
    assert output["execution_enabled"] is False
    assert output["authorization_verified"] is False


def test_choice_needs_only_one_confirmation_but_does_not_execute(start_card):
    path, _ = start_card
    code, output = invoke(path, "--choice", "verify")
    assert code == 2
    assert output["state"] == "awaiting_confirmation"
    record = confirmation(path, output)
    before = {str(item): item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}
    code, checked = invoke(path, "--choice", "verify", "--confirmation", record)
    assert code == 0
    assert checked["state"] == "preparation_complete"
    assert checked["confirmation_binding_matches"] is True
    assert checked["authorization_verified"] is False
    assert checked["execution_enabled"] is False
    assert checked["selected_option"] == "verify"
    assert checked["options"][0]["estimated_seconds_including_closeout"] == 1020
    assert before == {str(item): item.read_bytes() for item in path.parent.rglob("*") if item.is_file()}


@pytest.mark.parametrize("changes", [
    {"card_sha256": "f" * 64}, {"option_id": "other"}, {"revoked": True},
    {"revoked": "false"}, {"expires_at_utc": "2000-01-01T00:00:00Z"}
])
def test_stale_revoked_or_other_choice_confirmation_is_not_reused(start_card, changes):
    path, _ = start_card
    _, preview = invoke(path, "--choice", "verify")
    code, output = invoke(path, "--choice", "verify", "--confirmation", confirmation(path, preview, **changes))
    assert code == 2
    assert output["confirmation_binding_matches"] is False
    assert output["execution_enabled"] is False


def test_project_readme_drift_invalidates_the_card_even_with_old_confirmation(start_card):
    path, card = start_card
    _, preview = invoke(path, "--choice", "verify")
    record = confirmation(path, preview)
    Path(card["context"][0]["path"]).write_text("Changed authority and results.\n", encoding="utf-8")
    code, output = invoke(path, "--choice", "verify", "--confirmation", record)
    assert code == 2
    assert "context drift" in output["error"]
    assert output["execution_enabled"] is False


def test_more_time_does_not_add_actions_or_permissions(start_card):
    path, card = start_card
    _, initial = invoke(path)
    card["budgets"]["runtime_seconds"] = 7200
    save(path, card)
    code, larger = invoke(path)
    assert code == 2
    assert larger["options"][0]["action_ids"] == ["inspect", "draft"]
    assert larger["options"][0]["new_permissions"] == []
    assert larger["card_sha256"] != initial["card_sha256"]
    assert larger["execution_enabled"] is False


@pytest.mark.parametrize("resource,value", [
    ("runtime_seconds", 900), ("model_calls", -1), ("gpu_seconds", True), ("disk_bytes", None)
])
def test_invalid_or_insufficient_budget_blocks_selection(start_card, resource, value):
    path, card = start_card
    card["budgets"][resource] = value
    save(path, card)
    code, output = invoke(path, "--choice", "verify")
    assert code == 2
    assert output["execution_enabled"] is False
    assert output["state"] != "awaiting_confirmation"


def test_optional_branch_is_counted_before_recommending(start_card):
    path, card = start_card
    card["options"][0]["actions"][1]["estimate"]["seconds"] = 600
    save(path, card)
    code, output = invoke(path, "--choice", "verify")
    assert code == 2
    assert output["options"][0]["fits_budget"] is False
    assert "runtime_seconds" in output["options"][0]["blockers"]


@pytest.mark.parametrize("mutation", ["description", "context", "currency", "single-limit"])
def test_concrete_actions_and_single_action_limits_are_required(start_card, mutation):
    path, card = start_card
    action = card["options"][0]["actions"][0]
    if mutation == "description":
        action["description"] = ""
    elif mutation == "context":
        action["context_paths"] = [str(path.parent / "unbound.json")]
    elif mutation == "currency":
        card["budgets"]["currency"] = ""
    else:
        card["budgets"]["max_action_seconds"] = 500
    save(path, card)
    code, output = invoke(path, "--choice", "verify")
    assert code == 2
    assert output["state"] == "invalid_card"
    assert output["execution_enabled"] is False


@pytest.mark.parametrize("metered", [False, True])
def test_paid_actions_require_enforceable_cost_and_call_budgets(start_card, metered):
    path, card = start_card
    card["budgets"].update(model_calls=1, cost_minor=100, cost_metered=metered)
    card["options"][0]["actions"][0]["estimate"].update(model_calls=1, cost_minor=80)
    save(path, card)
    _, output = invoke(path, "--choice", "verify")
    assert output["options"][0]["fits_budget"] is metered
    assert output["execution_enabled"] is False
    if not metered:
        assert "unmetered_cost" in output["options"][0]["blockers"]


def test_unknown_estimate_is_reported_instead_of_inventing_a_price(start_card):
    path, card = start_card
    card["options"][0]["actions"][0]["estimate"]["cost_minor"] = None
    save(path, card)
    _, output = invoke(path, "--choice", "verify")
    assert output["options"][0]["fits_budget"] is False
    assert "unknown_cost_minor" in output["options"][0]["blockers"]


@pytest.mark.parametrize("mutation", ["missing-condition", "forward-dependency", "unknown-action", "arbitrary-command"])
def test_unbound_extensions_and_arbitrary_commands_are_rejected(start_card, mutation):
    path, card = start_card
    actions = card["options"][0]["actions"]
    if mutation == "missing-condition":
        actions[1]["condition"] = ""
    elif mutation == "forward-dependency":
        actions[0]["requires"] = ["draft"]
    elif mutation == "unknown-action":
        actions[0]["kind"] = "install"
    else:
        actions[0]["command"] = "anything"
    save(path, card)
    code, output = invoke(path, "--choice", "verify")
    assert code == 2
    assert "error" in output
    assert output["execution_enabled"] is False


def test_one_real_option_does_not_require_filler_alternatives(start_card):
    path, _ = start_card
    _, output = invoke(path)
    assert len(output["options"]) == 1
    assert output["recommended_option"] == "verify"


def test_new_permissions_are_visible_and_never_certified_by_local_matching(start_card):
    path, card = start_card
    card["options"][0]["new_permissions"] = ["候选区写入"]
    save(path, card)
    _, output = invoke(path, "--choice", "verify")
    record = confirmation(path, output)
    _, matched = invoke(path, "--choice", "verify", "--confirmation", record)
    assert matched["options"][0]["new_permissions"] == ["候选区写入"]
    assert matched["authorization_verified"] is False
    assert matched["execution_enabled"] is False


def test_missing_goal_and_expired_proposal_are_rejected(start_card):
    path, card = start_card
    card["goal"] = ""
    card["expires_at_utc"] = "2000-01-01T00:00:00Z"
    save(path, card)
    code, output = invoke(path)
    assert code == 2
    assert output["state"] == "invalid_card"


def test_duplicate_json_keys_and_outside_context_are_rejected(start_card):
    path, card = start_card
    path.write_text('{"schema":"x","schema":"y"}', encoding="utf-8")
    code, output = invoke(path)
    assert code == 2
    assert "duplicate" in output["error"]
    card["context"][0]["path"] = str(path)
    save(path, card)
    code, output = invoke(path)
    assert code == 2
    assert "outside project" in output["error"]
