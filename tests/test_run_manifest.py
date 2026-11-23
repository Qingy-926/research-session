import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "research-session" / "scripts"
SENSITIVE_ENV = ("RESEARCH_SESSION_PROVIDER_CONFIG", "RESEARCH_SESSION_RESPONSES_URL",
                 "RESEARCH_SESSION_MODEL", "RESEARCH_SESSION_API_KEY", "RESEARCH_SESSION_API_KEY_ENV")


def load_script(module_name, filename):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clear_provider_env(monkeypatch):
    for name in SENSITIVE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def manifest_module():
    return load_script("run_manifest_under_test", "run_manifest.py")


def test_redact_removes_bearer_and_secret_shaped_values(manifest_module, monkeypatch):
    monkeypatch.setenv("FIXTURE_PROVIDER_TOKEN", "sk-env-value-123456")
    payload = {
        "ok": False,
        "error": "upstream said Authorization: Bearer abc123 and rejected",
        "headers": {"Authorization": "Bearer abc123", "api_key_env": "FIXTURE_PROVIDER_TOKEN",
                    "api_key": "sk-live-abcdef123456", "token": "topsecret", "password": "hunter2"},
    }
    text = json.dumps(manifest_module.build(payload, 2, manifest_module.started_at()), ensure_ascii=False)
    for leaked in ("abc123", "topsecret", "hunter2", "FIXTURE_PROVIDER_TOKEN", "sk-"):
        assert leaked not in text
    assert "[REDACTED]" in text
    assert "sk-env-value-123456" not in text


def test_manifest_is_written_only_when_the_env_var_is_set(manifest_module, tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", raising=False)
    target = tmp_path / "manifests"
    assert manifest_module.record_run({"ok": True}, 0, manifest_module.started_at()) is None
    assert not target.exists()
    monkeypatch.setenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", str(target))
    path = manifest_module.record_run({"ok": True}, 0, manifest_module.started_at())
    assert path is not None and Path(path).is_file()
    assert json.loads(Path(path).read_text(encoding="utf-8"))["schema"] == "research-session/run-manifest/v1"
    assert len(list(target.glob("*.json"))) == 1


def test_audit_dispatch_writes_a_manifest_only_on_opt_in(tmp_path, monkeypatch):
    dispatch = load_script("manifest_audit_dispatch", "audit_dispatch.py")
    clear_provider_env(monkeypatch)
    monkeypatch.setattr(dispatch, "_request_with_deadline",
                        lambda *args, **kwargs: pytest.fail("network attempted"))
    monkeypatch.setattr(dispatch.sys, "argv", ["audit_dispatch.py", "--probe"])
    monkeypatch.delenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", raising=False)
    with pytest.raises(SystemExit) as stopped:
        dispatch.main()
    assert stopped.value.code == 2
    assert not (tmp_path / "manifests").exists()

    monkeypatch.setenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", str(tmp_path / "manifests"))
    with pytest.raises(SystemExit) as stopped:
        dispatch.main()
    assert stopped.value.code == 2
    written = list((tmp_path / "manifests").glob("*.json"))
    assert len(written) == 1
    manifest = json.loads(written[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "stopped"
    assert manifest["error"] == "provider_not_configured"
    assert manifest["config_sha256"] is None
    assert manifest["host"] == {"name": "unknown", "version": "unknown"}
    expected_version = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert manifest["component_versions"]["plugin"] == expected_version
