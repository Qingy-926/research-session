"""Offline golden evaluations for research-integrity behaviour.

All fixtures are synthetic, deterministic and network-free: no real project
material, no model, no provider. Each test is independent.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "research-session" / "scripts"
GUARD = SCRIPTS / "claim_citation_guard.py"


def run_guard(*arguments):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run([sys.executable, "-B", str(GUARD), *map(str, arguments)],
                               capture_output=True, text=True, encoding="utf-8", env=env)
    return completed.returncode, json.loads(completed.stdout.strip().splitlines()[-1])


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_golden_fake_citation_is_rejected(tmp_path):
    """Synthetic draft cites a source that does not exist, then a quote that is absent."""
    excerpt = tmp_path / "synthetic-excerpt.md"
    excerpt.write_text("Synthetic excerpt: accuracy rose on the clean split.\n", encoding="utf-8")
    sources = write_json(tmp_path / "sources.json", {"synthetic": True, "sources": [
        {"id": "S1", "verified": True, "path": "synthetic-excerpt.md",
         "quote": "accuracy rose on the clean split"},
        {"id": "S2", "verified": True},
        {"id": "S3", "verified": False},
    ]})
    draft = tmp_path / "draft.md"
    draft.write_text("# Synthetic draft\n\n支持 A [[cite:S1]]，另称 B [[cite:S9]]。\n", encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--sources", sources)
    assert (code, payload["stage"], payload["ok"], payload["code"]) == (2, "citation_guard", False, "unknown_citation")

    draft.write_text("# Synthetic draft\n\n支持 A [[cite:S3]]。\n", encoding="utf-8")
    assert run_guard("--draft", draft, "--sources", sources)[1]["code"] == "unverified_source"

    draft.write_text("# Synthetic draft\n\n支持 A [[cite:S1]]。\n", encoding="utf-8")
    sources = write_json(tmp_path / "sources.json", {"synthetic": True, "sources": [
        {"id": "S1", "verified": True, "path": "synthetic-excerpt.md", "quote": "this text is not in the file"}]})
    assert run_guard("--draft", draft, "--sources", sources)[1]["code"] == "quote_not_found"


def test_golden_conclusion_without_bound_result_is_rejected(tmp_path):
    """Synthetic writing: a conclusion number needs a [[result:ID]] binding."""
    evidence = write_json(tmp_path / "evidence.json", {"synthetic": True, "results": [
        {"id": "R1", "value": 0.912, "metric": "accuracy"}]})
    draft = tmp_path / "draft.md"
    draft.write_text("# Synthetic result section\n\n新方法把准确率提升了 4.2%，显著优于对照。\n", encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--evidence", evidence)
    assert (code, payload["code"]) == (2, "unbound_claim")

    draft.write_text("# Synthetic result section\n\n新方法把准确率提升了 4.2% [[result:R1]]，显著优于对照。\n",
                     encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--evidence", evidence)
    assert (code, payload["ok"], payload["code"]) == (0, True, None)

    draft.write_text("# Synthetic result section\n\n新方法把准确率提升了 4.2% [[result:R9]]。\n", encoding="utf-8")
    assert run_guard("--draft", draft, "--evidence", evidence)[1]["code"] == "unknown_result"


def test_golden_unconfigured_provider_rejects_without_request(tmp_path, monkeypatch):
    """Synthetic review pack: online dispatch without provider must not touch the network."""
    spec = importlib.util.spec_from_file_location("golden_audit_dispatch", SCRIPTS / "audit_dispatch.py")
    dispatch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatch)
    for name in ("RESEARCH_SESSION_PROVIDER_CONFIG", "RESEARCH_SESSION_RESPONSES_URL",
                 "RESEARCH_SESSION_MODEL", "RESEARCH_SESSION_API_KEY_ENV", "RESEARCH_SESSION_RUN_MANIFEST_DIR"):
        monkeypatch.delenv(name, raising=False)
    def no_network(*args, **kwargs):
        pytest.fail("network attempted without an explicit provider")
    for name in ("_request_with_deadline", "responses_post", "http_get_models", "http_post_responses"):
        monkeypatch.setattr(dispatch, name, no_network)
    pack = tmp_path / "synthetic-pack.md"
    pack.write_text("# Synthetic review pack\n\n复核点1 的合成包正文。\n", encoding="utf-8")
    evidence = tmp_path / "synthetic-evidence.md"
    evidence.write_text("synthetic evidence\n", encoding="utf-8")
    digest = __import__("hashlib").sha256(evidence.read_bytes()).hexdigest()
    inputs = write_json(tmp_path / "inputs.json", {
        "review_id": "synthetic-no-provider", "files": [{"path": "synthetic-evidence.md", "sha256": digest}]})
    monkeypatch.setattr(dispatch.sys, "argv", [
        "audit_dispatch.py", "--pack", str(pack), "--point", "1", "--inputs", str(inputs)])
    with pytest.raises(SystemExit) as stopped:
        dispatch.main()
    assert stopped.value.code == 2
    assert dispatch.LAST_EMITTED["stage"] == "provider"
    assert dispatch.LAST_EMITTED["error"] == "provider_not_configured"
    assert dispatch.LAST_EMITTED["request_started"] is False
    assert pack.read_text(encoding="utf-8") == "# Synthetic review pack\n\n复核点1 的合成包正文。\n"


def test_golden_citation_without_search_note_is_rejected(tmp_path):
    """Synthetic degraded search: citations require an explicit search-limitation note."""
    sources = write_json(tmp_path / "sources.json", {"synthetic": True, "sources": [
        {"id": "S1", "verified": True}]})
    draft = tmp_path / "draft.md"
    draft.write_text("# Synthetic related work\n\n已有工作支持该假设 [[cite:S1]]。\n", encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--sources", sources, "--require-search-note")
    assert (code, payload["code"]) == (2, "missing_search_note")

    draft.write_text("# Synthetic related work\n\n已有工作支持该假设 [[cite:S1]]。检索限制：仅覆盖合成库，未检出其他来源。\n",
                     encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--sources", sources, "--require-search-note")
    assert (code, payload["ok"], payload["code"]) == (0, True, None)


def test_golden_response_letter_cannot_invent_numbers(tmp_path):
    """Synthetic response letter: numbers need result bindings before a reply is sent."""
    evidence = write_json(tmp_path / "evidence.json", {"synthetic": True, "results": [
        {"id": "R1", "value": -0.031, "metric": "accuracy_delta"}]})
    draft = tmp_path / "response-letter.md"
    draft.write_text("# Synthetic response letter\n\n审稿人指出准确率下降了 3.1%，我们已重新核对。\n",
                     encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--evidence", evidence)
    assert (code, payload["code"]) == (2, "unbound_claim")

    draft.write_text("# Synthetic response letter\n\n审稿人指出准确率下降了 3.1% [[result:R1]]，我们已重新核对。\n",
                     encoding="utf-8")
    code, payload = run_guard("--draft", draft, "--evidence", evidence)
    assert (code, payload["ok"], payload["code"]) == (0, True, None)
