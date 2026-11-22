"""Shared Trae/Codex consistency suite; standard library plus pytest.

Offline only: no network, no provider, and nothing is written to ~/.trae-cn,
~/.codex or any other host cache. Temporary files stay under pytest's tmp_path.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
CONTRACTS = ROOT / "contracts"
SCRIPTS = SKILLS / "research-session" / "scripts"
MANIFESTS = {
    "trae": ROOT / ".trae-plugin" / "plugin.json",
    "codex": ROOT / ".codex-plugin" / "plugin.json",
}
PLACEHOLDERS = {"", "local developer", "developer", "your name", "todo", "tbd"}
PROVIDER_ENV = ("RESEARCH_SESSION_PROVIDER_CONFIG", "RESEARCH_SESSION_RESPONSES_URL",
                "RESEARCH_SESSION_MODEL", "RESEARCH_SESSION_API_KEY", "RESEARCH_SESSION_API_KEY_ENV",
                "RESEARCH_SESSION_RUN_MANIFEST_DIR", "RESEARCH_SESSION_LEDGER_DIR")
BACKTICK = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\]\(([^)\s#]+)\)")
ROUTE_PATH = re.compile(r"^(?:skills|references)/[A-Za-z0-9._/-]+$")


def load_script(module_name, filename):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_contract(name):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def read_skill_header(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", path
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields


@pytest.fixture(scope="module")
def manifests():
    return {host: json.loads(path.read_text(encoding="utf-8")) for host, path in MANIFESTS.items()}


def test_both_manifests_exist_and_declare_the_same_public_metadata(manifests):
    assert set(manifests) == {"trae", "codex"}
    for path in MANIFESTS.values():
        assert path.is_file(), path
    for key in ("name", "version", "description", "author", "repository", "license",
                "keywords", "skills", "interface"):
        assert manifests["trae"][key] == manifests["codex"][key], key
    manifest = manifests["codex"]
    assert manifest["name"] == "research-session"
    assert manifest["version"] == "6.2.0"
    assert manifest["license"] == "Apache-2.0"
    assert manifest["repository"] == "https://github.com/Qingy-926/research-session"
    for value in (manifest["author"]["name"], manifest["interface"]["developerName"]):
        assert value.strip() and value.strip().casefold() not in PLACEHOLDERS
    assert (ROOT / manifest["skills"]).resolve() == SKILLS.resolve()


def test_skill_discovery_matches_the_manifest_path(manifests):
    skill_files = sorted(SKILLS.glob("*/SKILL.md"))
    assert len(skill_files) == 16
    names = set()
    for path in skill_files:
        fields = read_skill_header(path)
        assert fields.get("name") == path.parent.name, path
        assert fields.get("description"), path
        names.add(fields["name"])
    assert "research-session" in names
    assert len(names) == 15 + 1
    declared = (ROOT / manifests["codex"]["skills"]).resolve()
    assert {path.parent.name for path in declared.glob("*/SKILL.md")} == names


def test_entry_skill_routes_point_to_existing_files():
    entry = SKILLS / "research-session" / "SKILL.md"
    text = entry.read_text(encoding="utf-8")
    candidates = [match.group(1).strip() for match in BACKTICK.finditer(text)]
    candidates += [match.group(1).strip() for match in LINK.finditer(text)]
    checked = []
    for value in candidates:
        if not ROUTE_PATH.match(value):
            continue
        assert (entry.parent / value).resolve().exists(), value
        checked.append(value)
    assert len(checked) >= 5
    assert any(value.startswith("references/") for value in checked)
    routed_skills = {match.group(1).strip() for match in BACKTICK.finditer(text)}
    skill_names = {path.parent.name for path in SKILLS.glob("*/SKILL.md")}
    assert routed_skills & (skill_names - {"research-session"})


def test_run_manifest_round_trips_through_a_temp_directory(tmp_path):
    manifest_module = load_script("dual_host_run_manifest", "run_manifest.py")
    schema = read_contract("run-manifest.v1.json")
    receipt = tmp_path / "pack.md.review.json"
    receipt.write_text('{"schema": "synthetic"}\n', encoding="utf-8")
    reference = tmp_path / "evidence.md"
    reference.write_text("synthetic evidence\n", encoding="utf-8")
    payload = {
        "gate_passed": True,
        "receipt": str(receipt),
        "binding": {"files": [{"path": str(reference), "sha256": "0" * 64}],
                    "route": {"url": "https://provider.example/v1/responses",
                              "wire_model": "fixture-review-model",
                              "provider": "fixture-provider"}},
        "error": None,
    }
    built = manifest_module.build(payload, 0, manifest_module.started_at())
    path = manifest_module.emit_for_run(tmp_path / "runs", built)
    reread = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("schema", "run_id", "status", "config_sha256", "component_versions", "references", "artifacts"):
        assert reread[key] == built[key]
    assert set(schema["required"]) <= set(reread)
    assert reread["component_versions"]["plugin"] == "6.2.0"


def test_provider_and_args_errors_match_the_recovery_contract(monkeypatch, capsys):
    recovery = read_contract("recovery.v1.json")
    dispatch = load_script("dual_host_audit_dispatch", "audit_dispatch.py")
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dispatch, "_request_with_deadline",
                        lambda *args, **kwargs: pytest.fail("network attempted"))

    monkeypatch.setattr(dispatch.sys, "argv", ["audit_dispatch.py", "--probe"])
    with pytest.raises(SystemExit) as stopped:
        dispatch.main()
    assert stopped.value.code == 2
    probe = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert probe["error"] == "provider_not_configured"
    assert probe["error"] in recovery["provider_error_codes"]
    assert probe["stage"] in recovery["dispatch_stages"]

    monkeypatch.setattr(dispatch.sys, "argv", ["audit_dispatch.py"])
    with pytest.raises(SystemExit) as stopped:
        dispatch.main()
    assert stopped.value.code == 2
    missing = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert missing["stage"] == "args"
    assert missing["stage"] in recovery["error_stages"]


def test_pause_and_recovery_semantics_match_the_contract(tmp_path, monkeypatch):
    recovery = read_contract("recovery.v1.json")
    monkeypatch.delenv("RESEARCH_SESSION_FIXTURE_TEMP_ROOT", raising=False)
    runtime = load_script("dual_host_autonomy_run", "autonomy_run.py")
    ledger = load_script("dual_host_review_ledger", "review_ledger.py")
    assert issubclass(runtime.RunError, ValueError)
    project = tmp_path / "project-999-fixture"
    project.mkdir()
    assert runtime.fixture_root(project) == project.resolve()
    with pytest.raises(runtime.RunError):
        runtime.fixture_root(tmp_path / "project-123-real")
    blocked = ledger.blocking_reason({"invalid_fixture_corrections": [{"entry_id": "fixture"}]})
    assert blocked is not None
    assert blocked[0] in recovery["error_stages"]
    assert recovery["blocked_state"] == "BLOCKED"


def test_trae_host_loads_the_plugin():
    pytest.skip("requires installed Trae/Codex host: real plugin loading and skill listing are host-verified only")


def test_codex_marketplace_installs_the_plugin():
    pytest.skip("requires installed Trae/Codex host: marketplace installation is host-verified only")
