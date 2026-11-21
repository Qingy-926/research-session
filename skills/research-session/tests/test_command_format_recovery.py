"""Only recover the observed lossless command wrapper; never recover a pass."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/audit_dispatch.py"
spec = importlib.util.spec_from_file_location("command_format_dispatch", SOURCE)
assert spec is not None and spec.loader is not None
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)


def receipt(tmp_path, *, decision="未完成", round_number=0, argv=None):
    report = {"decision": decision, "summary": "original evidence issue",
              "checklist": [{"item": 1, "status": "通过" if decision == "通过" else "存疑", "reason": "evidence"}],
              "findings": [{"id": "B1", "kind": "advice" if decision == "通过" else "blocking", "domain": "evidence",
                  "location": "source", "evidence": "actual source", "impact": "missing evidence", "change": "supply source",
                  "acceptance": "source bound", "quantitative_claim": {"artifact": "source.py", "observed": 1,
                      "expected": 2, "command": {"argv": ["python", "-V"] if argv is None else argv, "cwd": "fixture"}}}],
              "closed_findings": []}
    raw = json.dumps(report, ensure_ascii=False)
    pack = tmp_path / "pack.md"
    pack.write_bytes(b"frozen prefix\n" + raw.encode("utf-8"))
    return {"report": None, "round": round_number, "review_counted": False, "previous": None,
            "error": "裁决合同未通过: reproduction requires an argv command or JSON pointer (not executed by lint)",
            "binding": {"rules_sha256": "449a487edeb6fb6f68d2ad046f9e2c0774b1659b7b9fe6bf63d0f7b9b577ed28"},
            "raw_report": raw, "pack": {"path": str(pack), "before_bytes": len(b"frozen prefix\n")}}


def test_command_wrapper_is_lossless_and_original_receipt_unchanged(tmp_path):
    row = receipt(tmp_path)
    original = copy.deepcopy(row)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    recovered, info = dispatch.recover_format_baseline(str(path), row, 1)
    assert row == original
    assert recovered["round"] == 1 and recovered["report"]["decision"] == "未完成"
    claim = recovered["report"]["findings"][0]["quantitative_claim"]
    assert claim["command"] == ["python", "-V"]
    assert claim["command_context"] == {"argv": ["python", "-V"], "cwd": "fixture"}
    assert info["effective_round"] == 1 and info["original_round"] == 0


@pytest.mark.parametrize("kwargs", [{"decision": "通过"}, {"round_number": 1}, {"argv": []}, {"argv": ["python", 2]}])
def test_pass_later_round_or_non_command_cannot_be_recovered(tmp_path, kwargs):
    row = receipt(tmp_path, **kwargs)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError):
        dispatch.recover_format_baseline(str(path), row, 1)
