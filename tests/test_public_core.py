import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DISPATCH = PLUGIN_ROOT / "skills" / "research-session" / "scripts" / "audit_dispatch.py"


def load_dispatch():
    spec = importlib.util.spec_from_file_location("public_core_dispatch", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_online_review_requires_explicit_provider_without_network(tmp_path, monkeypatch):
    dispatch = load_dispatch()
    monkeypatch.delenv("RESEARCH_SESSION_RESPONSES_URL", raising=False)
    monkeypatch.delenv("RESEARCH_SESSION_PROVIDER_CONFIG", raising=False)
    monkeypatch.delenv("RESEARCH_SESSION_MODEL", raising=False)
    monkeypatch.setattr(dispatch, "_request_with_deadline", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network attempted")))
    monkeypatch.setattr(dispatch.sys, "argv", ["audit_dispatch.py", "--probe"])
    with __import__("pytest").raises(SystemExit) as stopped:
        dispatch.main()
    assert stopped.value.code == 2


def test_provider_config_binds_public_route_and_reads_secret_only_from_env(tmp_path, monkeypatch):
    dispatch = load_dispatch()
    config = tmp_path / "provider.json"
    config.write_text(json.dumps({
        "url": "https://provider.example/v1/responses",
        "model": "fixture-review-model",
        "api_key_env": "FIXTURE_PROVIDER_TOKEN",
        "provider": "fixture-provider",
    }), encoding="utf-8")
    monkeypatch.setenv("FIXTURE_PROVIDER_TOKEN", "secret-fixture")
    args = type("Args", (), {"provider_config": str(config), "responses_url": None,
                             "port": None, "api_key_env": None})()
    route = dispatch.responses_route(args, "fixture-review-model")
    token = dispatch.provider_api_key(route)
    assert route == {"transport": "responses", "url": "https://provider.example/v1/responses",
                     "wire_model": "fixture-review-model", "provider": "fixture-provider",
                     "api_key_env": "FIXTURE_PROVIDER_TOKEN"}
    assert token == "secret-fixture"
    assert "secret-fixture" not in repr(route)


def test_public_runtime_has_no_private_machine_defaults():
    paths = [
        DISPATCH,
        DISPATCH.with_name("audit_state.py"),
        DISPATCH.with_name("audit_raw.py"),
        DISPATCH.with_name("shadow_compare.py"),
        PLUGIN_ROOT / "skills" / "research-session" / "references" / "plugin-invocation.md",
        PLUGIN_ROOT / "references" / "local-method-library.md",
    ]
    forbidden = ("huawei-curated", "HUAWEI", "project-028", "Project-028", "project-029",
                 ".cc-switch", "gpt-6.2-sol", "gpt-5.6-sol", "57321", "15721",
                 "kind-nibbling-ladybug")
    hits = [(str(path.relative_to(PLUGIN_ROOT)), token) for path in paths
            for token in forbidden if token in path.read_text(encoding="utf-8")]
    assert hits == []


def test_shadow_compare_requires_explicit_log_path():
    script = DISPATCH.with_name("shadow_compare.py")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, "-B", str(script), "--show"],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 2
    assert json.loads(result.stdout)["stage"] == "args"
