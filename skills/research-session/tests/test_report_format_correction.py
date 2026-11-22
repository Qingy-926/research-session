"""A format repair may preserve an adverse report, never create a review pass."""
import copy
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import audit_dispatch as audit


def original():
    return {
        "decision": "未完成", "summary": "Missing source; implementation issue remains.",
        "checklist": [{"item": 1, "status": "存疑", "reason": "Source evidence unavailable.",
                       "finding_ids": ["B1"], "missing_evidence": "original source /count"}],
        "claim_checks": [{"item": 1, "status": "通过", "reason": "Original case closed; new failure differs.",
                          "finding_ids": ["B1"]}],
        "findings": [{"id": "B1", "kind": "blocking", "domain": "evidence",
                      "location": "source /count", "evidence": "Missing original count.",
                      "impact": "Cannot validate denominator.", "change": "Provide count source.",
                      "acceptance": "Original source locatable.",
                      "quantitative_claim": {"artifact": "source.json", "observed": 1, "expected": 2,
                                             "json_pointer": ["/count", "/denominator"]},
                      "closure": {"mode": "recheck", "reason": "Source must be independently checked."}}],
        "closed_findings": [],
    }


def test_format_candidate_preserves_every_finding_and_original_report():
    source = original()
    before = copy.deepcopy(source)
    # Removing the unrelated blocker reference fixes the coverage contradiction.
    candidate = copy.deepcopy(source)
    candidate["claim_checks"][0]["finding_ids"] = []
    candidate["findings"][0]["quantitative_claim"].update(
        json_pointer="/count", json_pointer_context=["/count", "/denominator"])
    repaired = audit.validate_format_correction(source, candidate, 1,
        {"claims": ["Original acceptance case is closed"], "editable_reports": []})
    assert source == before
    assert repaired["decision"] == "未完成"
    assert repaired["findings"][0]["id"] == "B1"
    assert repaired["findings"][0]["quantitative_claim"]["json_pointer_context"] == ["/count", "/denominator"]
    assert repaired["checklist"][0]["missing_evidence"] == "original source /count"


@pytest.mark.parametrize("mutation", ["pass", "drop", "evidence", "reason", "pointer_loss", "new_claim"])
def test_format_changes_cannot_remove_evidence_blockers_or_alter_judgment(mutation):
    source = original()
    candidate = copy.deepcopy(source)
    candidate["claim_checks"][0]["finding_ids"] = []
    candidate["findings"][0]["quantitative_claim"].update(
        json_pointer="/count", json_pointer_context=["/count", "/denominator"])
    if mutation == "pass":
        candidate["decision"] = "通过"
    elif mutation == "drop":
        candidate["findings"] = []
    elif mutation == "evidence":
        candidate["findings"][0]["evidence"] = "Now resolved."
    elif mutation == "reason":
        candidate["claim_checks"][0]["reason"] = "Different scientific claim."
    elif mutation == "pointer_loss":
        candidate["findings"][0]["quantitative_claim"].pop("json_pointer_context")
    else:
        candidate["claim_checks"].append(copy.deepcopy(candidate["claim_checks"][0]))
    with pytest.raises(ValueError):
        audit.validate_format_correction(source, candidate, 1,
            {"claims": ["Original acceptance case is closed"], "editable_reports": []})


def test_remaining_coverage_contradiction_is_still_rejected():
    source = original()
    candidate = copy.deepcopy(source)
    candidate["findings"][0]["quantitative_claim"].update(
        json_pointer="/count", json_pointer_context=["/count", "/denominator"])
    with pytest.raises(ValueError):
        audit.validate_format_correction(source, candidate, 1,
            {"claims": ["Original acceptance case is closed"], "editable_reports": []})


@pytest.mark.parametrize("old,new", [(1, True), (0, False), (1, 1.0),
                                   ({"nested": [1]}, {"nested": [True]})])
def test_format_correction_preserves_json_value_types(old, new):
    source = original()
    source["findings"][0]["quantitative_claim"]["observed"] = old
    candidate = audit.normalized_format_report(source)
    candidate["claim_checks"][0]["finding_ids"] = []
    candidate["findings"][0]["quantitative_claim"]["observed"] = new
    with pytest.raises(ValueError):
        audit.validate_format_correction(source, candidate, 1,
            {"claims": ["Original acceptance case is closed"], "editable_reports": []})
