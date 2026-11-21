import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_support

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
SCRIPTS = ROOT / "skills" / "research-session" / "scripts"
CONTRACT_FILES = ("provider-adapter.v1.json", "run-manifest.v1.json", "recovery.v1.json")


def load_script(module_name, filename):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dispatch():
    return load_script("contracts_audit_dispatch", "audit_dispatch.py")


@pytest.fixture(scope="module")
def manifest_module():
    return load_script("contracts_run_manifest", "run_manifest.py")


def test_contract_files_declare_their_version_and_id():
    assert (CONTRACTS / "README.md").is_file()
    for name in CONTRACT_FILES:
        data = contract(name)
        assert data["contract_version"] == "v1", name
        assert data["$id"] == "https://github.com/Qingy-926/research-session/contracts/" + name


def test_provider_schema_matches_the_keys_the_script_accepts(dispatch, tmp_path):
    schema = contract("provider-adapter.v1.json")
    assert set(schema["properties"]) == {"url", "model", "provider", "api_key_env"}
    assert schema["additionalProperties"] is False
    extra = tmp_path / "provider-extra.json"
    extra.write_text(json.dumps({"url": "https://provider.example/v1/responses", "shared_token": "x"}),
                     encoding="utf-8")
    args = argparse.Namespace(provider_config=str(extra), responses_url=None, port=None, api_key_env=None)
    with pytest.raises(dispatch.ProviderNotConfigured):
        dispatch._provider_config(args)
    assert contract_support.validate(schema, {"url": "https://provider.example/v1/responses",
                                              "model": "fixture-model", "provider": "fixture-provider",
                                              "api_key_env": "FIXTURE_PROVIDER_TOKEN"}) == []


def test_resolved_route_validates_against_the_provider_schema(dispatch):
    schema = contract("provider-adapter.v1.json")
    args = argparse.Namespace(
        provider_config=json.dumps({"url": "https://provider.example/v1/responses",
                                    "model": "fixture-review-model",
                                    "provider": "fixture-provider",
                                    "api_key_env": "FIXTURE_PROVIDER_TOKEN"}),
        responses_url=None, port=None, api_key_env=None)
    route = dispatch.responses_route(args, None)
    resolved = {"url": route["url"], "model": route["wire_model"], "provider": route["provider"],
                "api_key_env": route["api_key_env"]}
    assert contract_support.validate(schema, resolved) == []


def test_run_manifest_build_validates_and_round_trips(manifest_module, tmp_path):
    schema = contract("run-manifest.v1.json")
    receipt = tmp_path / "pack.md.review.json"
    receipt.write_text('{"schema": "synthetic"}\n', encoding="utf-8")
    reference = tmp_path / "evidence.md"
    reference.write_text("synthetic evidence\n", encoding="utf-8")
    payload = {"ok": True, "gate_passed": True, "receipt": str(receipt),
               "binding": {"files": [{"path": str(reference), "sha256": "0" * 64}],
                           "route": {"url": "https://provider.example/v1/responses",
                                     "wire_model": "fixture-review-model", "provider": "fixture-provider",
                                     "api_key_env": "FIXTURE_PROVIDER_TOKEN"}},
               "error": None}
    manifest = manifest_module.build(payload, 0, manifest_module.started_at())
    assert manifest["status"] == "gate_passed"
    assert contract_support.validate(schema, manifest) == []
    path = manifest_module.emit_for_run(tmp_path, manifest)
    assert json.loads(Path(path).read_text(encoding="utf-8")) == manifest
    completed = manifest_module.build({"ok": True}, 0, manifest_module.started_at())
    assert completed["status"] == "completed"
    assert contract_support.validate(schema, completed) == []
    assert completed["config_sha256"] is None


def test_recovery_enums_match_the_scripts_real_output():
    recovery = contract("recovery.v1.json")
    dispatch_text = (SCRIPTS / "audit_dispatch.py").read_text(encoding="utf-8")
    stages = set(re.findall(r'"stage":\s*"([a-z_]+)"', dispatch_text))
    assert stages == set(recovery["dispatch_stages"])
    ledger_text = (SCRIPTS / "review_ledger.py").read_text(encoding="utf-8")
    ledger_stages = set(re.findall(r'return\s+\("([a-z_]+)",', ledger_text))
    assert ledger_stages == set(recovery["ledger_stages"])
    assert set(recovery["error_stages"]) <= stages | ledger_stages
    assert set(recovery["recovery_stages"]) <= stages
    assert set(recovery["fixture_stages"]) <= stages
    for code in recovery["provider_error_codes"]:
        assert '"%s"' % code in dispatch_text
    for disposition in recovery["receipt_dispositions"]:
        assert '"%s"' % disposition in dispatch_text, disposition
    autonomy_text = (SCRIPTS / "autonomy_run.py").read_text(encoding="utf-8")
    for state in recovery["run_states"] + recovery["action_states"] + [recovery["blocked_state"]]:
        assert '"%s"' % state in autonomy_text, state
