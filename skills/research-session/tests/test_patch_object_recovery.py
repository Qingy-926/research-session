"""Recover only the known first-round patch wrapper without granting a pass."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/audit_dispatch.py"
spec = importlib.util.spec_from_file_location("patch_format_dispatch", SOURCE)
assert spec is not None and spec.loader is not None
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)

ERROR = "\u88c1\u51b3\u5408\u540c\u672a\u901a\u8fc7: repair.patch\u987b\u4e3a\u975e\u7a7a\u5b57\u7b26\u4e32\uff0ctest_case\u987b\u4e3a\u975e\u7a7a\u5b57\u7b26\u4e32\u6216\u5bf9\u8c61"
REVISE = "\u5f85\u6574\u6539"
PASS = "\u901a\u8fc7"
RULES = "bc3bc3a47b5de01aa2e459b85076cffbf15cdb3de8e6023cc9d14aa80852e901"


def fixture(tmp_path, *, report_change=None, receipt_change=None):
    report = {
        "decision": REVISE, "summary": "one implementation defect",
        "checklist": [{"item": 1, "status": PASS, "reason": "bound evidence"}],
        "findings": [{
            "id": "B1", "kind": "blocking", "domain": "implementation",
            "location": "runtime.py:publish", "evidence": "final CPU snapshot is omitted",
            "impact": "resource gate can publish over budget", "change": "include final CPU",
            "acceptance": "reject the reachable over-budget case",
            "repair": {"status": "patch_proposed", "reason": "local accounting fix",
                       "targets": [{"path": "runtime.py", "sha256": "a" * 64}],
                       "patch": {"format": "unified_diff", "text": "-old\n+new\n", "context": [1, 2]},
                       "test_case": {"input": 3, "expected": "reject"}},
        }],
        "closed_findings": [],
    }
    if report_change:
        report_change(report)
    raw = json.dumps(report, ensure_ascii=False)
    prefix = b"frozen review body\n"
    content = prefix + b"original report\n" + raw.encode("utf-8") + b"\n"
    pack = tmp_path / "pack.md"
    pack.write_bytes(content)
    row = {
        "schema": "research-session/review/v1", "report": None, "round": 0,
        "review_counted": False, "previous": None, "error": ERROR,
        "binding": {"rules_sha256": RULES, "pack_sha256": hashlib.sha256(prefix).hexdigest()},
        "raw_report": raw,
        "pack": {"path": str(pack), "before_bytes": len(prefix),
                 "after_sha256": hashlib.sha256(content).hexdigest()},
    }
    if receipt_change:
        receipt_change(row)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return path, row, report


def test_known_patch_object_recovers_losslessly_without_mutating_originals(tmp_path):
    path, row, original = fixture(tmp_path)
    before = copy.deepcopy(row)
    receipt_bytes = path.read_bytes()
    pack_bytes = Path(row["pack"]["path"]).read_bytes()
    recovered, info = dispatch.recover_format_baseline(str(path), row, 1)
    assert row == before and path.read_bytes() == receipt_bytes
    assert Path(row["pack"]["path"]).read_bytes() == pack_bytes
    assert recovered["round"] == 1 and recovered["review_counted"] is True
    assert recovered["report"]["decision"] == REVISE
    assert recovered["raw_report"] == row["raw_report"]
    assert [f["id"] for f in recovered["report"]["findings"]] == ["B1"]
    assert json.loads(recovered["report"]["findings"][0]["repair"]["patch"]) == original["findings"][0]["repair"]["patch"]
    assert info["recovery_kind"] == "patch_object_wrapper"
    assert info["original_round"] == 0 and info["effective_round"] == 1
    assert info["source_sha256"] == hashlib.sha256(receipt_bytes).hexdigest()
    assert not recovered.get("gate_passed") and not recovered.get("review_completed")


@pytest.mark.parametrize("change", [
    lambda r: r["binding"].update(rules_sha256="0" * 64),
    lambda r: r.update(error="unregistered error"),
    lambda r: r.update(round=1),
    lambda r: r.update(review_counted=True),
    lambda r: r.update(previous="earlier.review.json"),
    lambda r: r.update(report={"decision": REVISE}),
])
def test_unknown_version_error_or_nonfirst_failure_is_rejected(tmp_path, change):
    path, row, _ = fixture(tmp_path, receipt_change=change)
    with pytest.raises(ValueError):
        dispatch.recover_format_baseline(str(path), row, 1)


@pytest.mark.parametrize("change", [
    lambda r: r.update(decision=PASS),
    lambda r: r["findings"][0]["repair"].update(patch={}),
    lambda r: r["findings"][0]["repair"].update(patch="already a string"),
    lambda r: r["findings"][0]["repair"].update(test_case=""),
    lambda r: r["findings"][0].pop("acceptance"),
    lambda r: r["findings"][0]["repair"].update(targets=[]),
])
def test_pass_empty_wrapper_and_other_contract_defects_are_not_recovered(tmp_path, change):
    path, row, _ = fixture(tmp_path, report_change=change)
    with pytest.raises(ValueError):
        dispatch.recover_format_baseline(str(path), row, 1)


def test_raw_opinion_must_match_bound_package_suffix(tmp_path):
    path, row, _ = fixture(tmp_path)
    row["raw_report"] = row["raw_report"].replace("one implementation defect", "changed decision context")
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError):
        dispatch.recover_format_baseline(str(path), row, 1)


def test_bound_package_tampering_is_rejected(tmp_path):
    path, row, _ = fixture(tmp_path)
    pack = Path(row["pack"]["path"])
    pack.write_bytes(pack.read_bytes().replace(b"frozen", b"edited"))
    with pytest.raises(ValueError):
        dispatch.recover_format_baseline(str(path), row, 1)


def test_normal_parser_is_not_relaxed_for_new_patch_objects(tmp_path):
    _, row, _ = fixture(tmp_path)
    with pytest.raises(ValueError):
        dispatch.parse_review(row["raw_report"], 1)


def test_recovered_blocker_is_required_in_incremental_closure(tmp_path):
    path, row, _ = fixture(tmp_path)
    recovered, _ = dispatch.recover_format_baseline(str(path), row, 1)
    next_report = {"decision": PASS, "summary": "fixed", "checklist": [{"item": 1, "status": PASS}],
                   "findings": [], "closed_findings": []}
    with pytest.raises(ValueError):
        dispatch.parse_review(json.dumps(next_report), 1, recovered)
    next_report["closed_findings"] = [{"id": "B1", "status": "\u5df2\u89e3\u51b3", "evidence": "bound regression"}]
    parsed = dispatch.parse_review(json.dumps(next_report), 1, recovered)
    assert parsed["closed_findings"][0]["id"] == "B1"
