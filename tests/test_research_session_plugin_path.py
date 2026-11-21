import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN_ROOT / "skills" / "research-session" / "scripts" / "audit_state.py"


def portable_path(path):
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return Path("\\\\?\\" + value)
    return path


@pytest.mark.parametrize("present", [True, False], ids=["present", "missing"])
def test_whitelist_checks_explicit_plugin_without_relaxing_guards(tmp_path, present):
    plugin_root = portable_path(tmp_path / "p")
    cli = plugin_root / "scripts" / "repro_governance.py"
    if present:
        cli.parent.mkdir(parents=True)
        cli.touch()
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check-plugin-whitelist", "--repo-root", str(tmp_path),
         "--plugin-root", str(plugin_root)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["plugin_main_cli_exists"] is present
    assert payload["ok"] is True
    assert "snapshot-code" in payload["write_subcommands_denied"]
    assert "freeze-protocol" in payload["write_subcommands_denied"]
    assert "archive" in payload["unusable_subcommands"]
    assert not set(payload["readonly_subcommands"]) & set(payload["write_subcommands_denied"])


def test_linked_shared_entry_finds_the_complete_plugin(tmp_path):
    shared = tmp_path / "shared-entry"
    target = PLUGIN_ROOT / "skills" / "research-session"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(shared), str(target)],
                       capture_output=True, check=True)
    else:
        shared.symlink_to(target, target_is_directory=True)
    result = subprocess.run(
        [sys.executable, "-B", str(shared / "scripts" / "audit_state.py"),
         "--check-plugin-whitelist", "--repo-root", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["plugin_main_cli_exists"] is True
