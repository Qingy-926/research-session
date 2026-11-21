import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


SKILL = Path(__file__).resolve().parents[1] / "skills" / "research-session"
MANIFEST = SKILL / "references" / "review-rules-compatibility.json"
PRE_TRANSLATION = "d26e77daa65d168d2e8c4bf1293243548b50d8d821dd08c3e8b38bb938a4d9f6"
PREVIOUS_V215 = "9bdc84e68489b4c78d7b27627e86edb80a8c43c44e1d470d9442214677090670"
BOUND_FILES = [
    "scripts/audit_dispatch.py",
    "references/codex-review-pack.md",
    "SKILL.md",
    "references/autonomy-bounds.md",
    "references/review-evidence.md",
    "scripts/review_evidence.py",
    "scripts/review_ledger.py",
    "references/project-work.md",
    "references/preregistration.md",
    "references/independent-review.md",
    "references/autonomous-start.md",
    "references/retrieval-evidence.md",
    "references/claim-evidence.md",
]


@pytest.fixture
def dispatch(monkeypatch):
    monkeypatch.syspath_prepend(str(SKILL / "scripts"))
    spec = importlib.util.spec_from_file_location("plugin_audit_dispatch", SKILL / BOUND_FILES[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def binding():
    digest = hashlib.sha256()
    for name in BOUND_FILES:
        digest.update((SKILL / name).read_bytes())
    raw = MANIFEST.read_bytes()
    return {"rules_sha256": digest.hexdigest(), "compatibility_manifest_sha256": hashlib.sha256(raw).hexdigest()}


def test_translated_description_keeps_exact_pre_translation_compatibility(dispatch, binding):
    assert json.loads(MANIFEST.read_bytes())["current_rules_sha256"] == binding["rules_sha256"]
    result = dispatch.compatible_rules_transition(PRE_TRANSLATION, binding)
    assert result["previous_rules_sha256"] == PRE_TRANSLATION
    assert result["current_rules_sha256"] == binding["rules_sha256"]


def test_previous_v215_receipt_compatibility(dispatch, binding):
    result = dispatch.compatible_rules_transition(PREVIOUS_V215, binding)
    assert result["previous_rules_sha256"] == PREVIOUS_V215
    assert result["current_rules_sha256"] == binding["rules_sha256"]


def test_compatibility_rejects_unknown_rules_and_wrong_current(dispatch, binding):
    with pytest.raises(ValueError):
        dispatch.compatible_rules_transition("f" * 64, binding)
    with pytest.raises(ValueError):
        dispatch.compatible_rules_transition(PRE_TRANSLATION, {**binding, "rules_sha256": "a" * 64})


def test_compatibility_rejects_changed_manifest_after_binding(dispatch, binding, monkeypatch):
    original = dispatch.open_bytes
    monkeypatch.setattr(dispatch, "open_bytes", lambda path: original(path) + b" ")
    with pytest.raises(ValueError, match="清单在预检中发生变化"):
        dispatch.compatible_rules_transition(PRE_TRANSLATION, binding)


def test_review_request_gives_actionable_scope_for_each_round(dispatch):
    first = dispatch.build_prompt(1, "fixture", ["check"], "fixture evidence")
    previous = {"report": {"findings": [{
        "id": "B1", "kind": "blocking",
    }]}}
    second = dispatch.build_prompt(1, "fixture", ["check"], "changed evidence", previous)
    assert "首轮一次列全当前阶段可证实的阻断" in first
    assert "修改位置、具体改法及验收条件" in first
    assert "无关的新问题只有在新证据使旧结论失效" in second
    assert "数据泄漏、协议矛盾或缺原始证据等科学硬门" in second
    assert "原硬门仍须阻断" in second


def test_structured_closure_evidence_is_preserved_without_changing_decision(dispatch):
    previous = {"report": {"findings": [{"id": "B1", "kind": "blocking"}]}}
    evidence = {"source_category": "local", "actual_evidence": "verified fixture", "path": "source.json"}
    report = {"decision": "通过", "summary": "fixture", "checklist": [
        {"item": 1, "status": "通过", "reason": ""}
    ], "findings": [], "closed_findings": [
        {"id": "B1", "status": "已解决", "evidence": evidence}
    ]}
    parsed = dispatch.parse_review(json.dumps(report), 1, previous)
    assert parsed["decision"] == "通过"
    assert json.loads(parsed["closed_findings"][0]["evidence"]) == evidence
    assert report["closed_findings"][0]["evidence"] == evidence


@pytest.mark.parametrize("evidence", [{}, {"actual_evidence": ""}, {"path": "source.json"}])
def test_structured_closure_without_actual_evidence_is_rejected(dispatch, evidence):
    previous = {"report": {"findings": [{"id": "B1", "kind": "blocking"}]}}
    report = {"decision": "通过", "summary": "fixture", "checklist": [
        {"item": 1, "status": "通过", "reason": ""}
    ], "findings": [], "closed_findings": [
        {"id": "B1", "status": "已解决", "evidence": evidence}
    ]}
    with pytest.raises(ValueError):
        dispatch.parse_review(json.dumps(report), 1, previous)
