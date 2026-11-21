import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "review_preflight.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    dispatch = load_module("local_fixture_dispatch", ROOT / "scripts/audit_dispatch.py")
    source = tmp_path / "metrics.json"
    source.write_text('{"metric":0.1}\n', encoding="utf-8")
    pack = tmp_path / "review-pack.md"
    pack.write_text("Synthetic metric 0.1, not a real review.\n", encoding="utf-8")
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"review_id": "synthetic-point2", "files": [
        {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    ]}), encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Synthetic continuity\nPoint 2 previously passed.\n"
        "Inputs: inputs.json\nReceipt: receipt.json\n"
        "Authorization: diagnose and recompute only; no rerun or review send.\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(inputs=str(inputs), pack=str(pack), reference=dispatch.REFERENCE_MD, point=2,
                              transport="responses", local_only=True)
    binding = dispatch.make_binding(args, dispatch.POINT_MODEL[2], hashlib.sha256(pack.read_bytes()).hexdigest())
    _, items = dispatch.load_checklist(2, dispatch.REFERENCE_MD)
    report = dispatch.review_contract(len(items))["response_template"]
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "schema": "research-session/review/v1", "synthetic_fixture": True,
        "round": 1, "binding": binding, "report": report,
        "pack": {"path": str(pack), "before_bytes": pack.stat().st_size,
                 "after_sha256": hashlib.sha256(pack.read_bytes()).hexdigest()},
    }), encoding="utf-8")
    return {"root": tmp_path, "dispatch": dispatch, "source": source, "pack": pack, "inputs": inputs, "receipt": receipt}


def invoke(fixture, *, receipt=True):
    assert SCRIPT.is_file(), "read-only preflight entry is missing"
    command = [sys.executable, "-X", "utf8", "-B", str(SCRIPT), "--pack", str(fixture["pack"]),
               "--point", "2", "--inputs", str(fixture["inputs"])]
    if receipt:
        command.extend(["--receipt", str(fixture["receipt"])])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.stdout.strip().startswith("{"), result.stderr
    return result.returncode, json.loads(result.stdout)


def test_preflight_reports_actual_loaded_source_and_matching_rules(fixture):
    code, result = invoke(fixture)
    assert code == 0
    assert result["ready"] is True
    assert result["receipt_current"] is True
    assert result["runtime"]["skill_root"] == str(ROOT)
    assert result["runtime"]["dispatch_path"] == str(ROOT / "scripts/audit_dispatch.py")
    assert result["runtime"]["dispatch_sha256"] == hashlib.sha256((ROOT / "scripts/audit_dispatch.py").read_bytes()).hexdigest()
    manifest = json.loads((ROOT / "references/review-rules-compatibility.json").read_bytes())
    assert result["binding"]["rules_sha256"] == manifest["current_rules_sha256"]
    assert result["gate_passed"] is False
    assert result["review_completed"] is False
    assert result["blocked_steps"] == ["register", "finalize", "automatic_rerun"]


@pytest.mark.parametrize("valid", [True, False], ids=["ready", "refused"])
def test_preflight_records_opted_in_normal_returns(fixture, monkeypatch, valid):
    manifests = fixture["root"] / "run-manifests"
    monkeypatch.setenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", str(manifests))
    if not valid:
        fixture["source"].write_text('{"metric":0.2}\n', encoding="utf-8")
    code, result = invoke(fixture, receipt=False)
    assert code == (0 if valid else 2)
    assert result["ready"] is valid
    written = list(manifests.glob("*.json"))
    assert len(written) == 1
    manifest = json.loads(written[0].read_text(encoding="utf-8"))
    assert manifest["status"] == ("completed" if valid else "stopped")
    assert manifest["error"] == result.get("error")


def test_historical_responses_binding_is_checked_without_claiming_live_route(fixture):
    receipt = json.loads(fixture["receipt"].read_bytes())
    route = {"transport": "responses", "url": "https://provider.example/v1/responses",
             "wire_model": "offline-unconfigured", "provider": "fixture-provider",
             "api_key_env": "FIXTURE_PROVIDER_TOKEN"}
    receipt["binding"]["route"] = route
    fixture["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
    before = fixture["receipt"].read_bytes()
    code, result = invoke(fixture)
    assert code == 0
    assert result["receipt_current"] is True
    assert result["binding"]["route"] == route
    assert result["route_validation"] == "historical_binding_only"
    assert result["current_route_verified"] is False
    assert result["gate_passed"] is False
    assert result["blocked_steps"] == ["register", "finalize", "automatic_rerun"]
    assert fixture["receipt"].read_bytes() == before


def test_registered_legacy_route_remains_read_only_and_keeps_its_verdict(fixture):
    receipt = json.loads(fixture["receipt"].read_bytes())
    route = {"transport": "codex-plus", "url": "https://gateway.example/v1/responses",
             "wire_model": "offline-unconfigured"}
    receipt["binding"]["route"] = route
    receipt["binding"]["rules_sha256"] = (
        "7d2557eacb53a81fc7871b1d073c6ace660b3d422997498146065a86773322e1")
    fixture["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
    before = fixture["receipt"].read_bytes()
    code, result = invoke(fixture)
    assert code == 2
    assert result["compatible_for_continuation"] is True
    assert result["binding"]["route"] == route
    assert result["route_validation"] == "historical_binding_only"
    assert result["current_route_verified"] is False
    assert result["receipt_current"] is False
    assert result["gate_passed"] is False
    assert result["review_completed"] is False
    assert fixture["receipt"].read_bytes() == before


@pytest.mark.parametrize("route", [None, "responses", {"transport": "legacy"}])
def test_malformed_historical_route_is_not_treated_as_current(fixture, route):
    receipt = json.loads(fixture["receipt"].read_bytes())
    receipt["binding"]["route"] = route
    fixture["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
    code, result = invoke(fixture)
    assert code == 2
    assert result["receipt_current"] is False
    assert result["current_route_verified"] is False
    assert "historical route binding" in result["error"]


@pytest.mark.parametrize("refresh_inputs", [False, True])
def test_new_process_rejects_changed_results_without_rewriting_receipt(fixture, refresh_inputs):
    original_receipt = fixture["receipt"].read_bytes()
    fixture["source"].write_text('{"metric":0.9}\n', encoding="utf-8")
    if refresh_inputs:
        value = json.loads(fixture["inputs"].read_bytes())
        value["files"][0]["sha256"] = hashlib.sha256(fixture["source"].read_bytes()).hexdigest()
        fixture["inputs"].write_text(json.dumps(value), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in fixture["root"].iterdir() if path.is_file()}
    code, result = invoke(fixture)
    assert code == 2
    assert result["receipt_current"] is False
    assert result["gate_passed"] is False
    assert result["review_completed"] is False
    assert result["blocked_steps"] == ["register", "finalize", "automatic_rerun"]
    assert "recompute_metrics_with_existing_authorization" in result["available_work"]
    assert fixture["receipt"].read_bytes() == original_receipt
    assert {path.name: path.read_bytes() for path in fixture["root"].iterdir() if path.is_file()} == before


@pytest.mark.parametrize("responses_receipt", [False, True])
def test_preflight_never_reads_provider_or_ledger(fixture, monkeypatch, capsys, responses_receipt):
    assert SCRIPT.is_file(), "read-only preflight entry is missing"
    module = load_module("local_preflight_guarded", SCRIPT)

    def denied(*args, **kwargs):
        raise AssertionError("provider or ledger path must not be called")

    monkeypatch.setattr(module.dispatch, "read_current_provider", denied)
    monkeypatch.setattr(module.dispatch, "_provider_config", denied)
    monkeypatch.setattr(module.dispatch.review_ledger, "preflight", denied)
    monkeypatch.setattr(module.dispatch, "post_with_retry", denied)
    command = ["--pack", str(fixture["pack"]), "--point", "2", "--inputs", str(fixture["inputs"])]
    if responses_receipt:
        receipt = json.loads(fixture["receipt"].read_bytes())
        receipt["binding"]["route"] = {
            "transport": "responses", "url": "https://provider.example/v1/responses",
            "wire_model": "offline-unconfigured", "provider": "fixture-provider",
            "api_key_env": "FIXTURE_PROVIDER_TOKEN"}
        fixture["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
        command.extend(["--receipt", str(fixture["receipt"])])
    code = module.main(command)
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["local_only"] is True
    assert result["gate_passed"] is False
    assert result["blocked_steps"] == ["register", "finalize", "automatic_rerun"]


def test_unknown_old_rules_cannot_be_whitelisted_by_version_name(fixture):
    receipt = json.loads(fixture["receipt"].read_bytes())
    receipt["binding"]["rules_sha256"] = "f" * 64
    fixture["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
    code, result = invoke(fixture)
    assert code == 2
    assert result["compatible_for_continuation"] is False
    assert result["receipt_current"] is False


def test_exact_known_rules_transition_is_not_inherited_approval(fixture):
    receipt = json.loads(fixture["receipt"].read_bytes())
    manifest = json.loads((ROOT / "references/review-rules-compatibility.json").read_bytes())
    previous = next(iter(manifest["compatible_previous"]))
    receipt["binding"]["rules_sha256"] = previous
    fixture["receipt"].write_text(json.dumps(receipt), encoding="utf-8")
    code, result = invoke(fixture)
    assert code == 2
    assert result["compatible_for_continuation"] is True
    assert result["rules_transition"]["previous_rules_sha256"] == previous
    assert result["receipt_current"] is False
    assert result["gate_passed"] is False


def test_documented_contract_command_uses_loaded_root_not_shared_copy():
    reference = (ROOT / "references/codex-review-pack.md").read_text(encoding="utf-8")
    lines = [line.strip() for line in reference.splitlines() if line.strip().startswith("python ") and "--show-contract" in line]
    assert len(lines) == 1
    import shlex
    command = lines[0].replace('"$RS_SKILL_ROOT/scripts/audit_dispatch.py"', shlex.quote(str(ROOT / "scripts/audit_dispatch.py")))
    argv = shlex.split(command, posix=True)
    assert Path(argv[1]).resolve() == (ROOT / "scripts/audit_dispatch.py").resolve()
    result = subprocess.run([sys.executable, "-B", *argv[1:]], capture_output=True, text=True, encoding="utf-8", timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0
    assert json.loads(result.stdout)["local_only"] is True
