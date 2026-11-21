import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/research-session/scripts/evidence_context.py"


def module():
    assert SCRIPT.is_file(), "bounded evidence context builder is missing"
    spec = importlib.util.spec_from_file_location("evidence_context_under_test", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sources(tmp_path, rows=None):
    paper = tmp_path / "paper.txt"
    paper.write_text("Section 3. A narrow observation.\nThe comparison uses a fixed protocol.\n", encoding="utf-8")
    rows = rows or [{"id": "paper-a", "verified": True, "path": "paper.txt",
                     "quote": "A narrow observation.", "locator": "Section 3",
                     "access": "full_text"}]
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"sources": rows}), encoding="utf-8")
    return path, rows


def test_context_keeps_exact_quote_locator_and_does_not_certify_support(tmp_path):
    path, _ = sources(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = module().build_context(path, ["paper-a"], 1000)
    assert result["ok"] is True
    assert "[[cite:paper-a]]" in result["context"]
    assert "Section 3" in result["context"]
    assert "A narrow observation." in result["context"]
    assert result["semantic_support_verified"] is False
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("field,value", [
    ("quote", "An invented result."), ("locator", ""), ("verified", False),
    ("access", "unavailable"), ("source_sha256", "0" * 64),
])
def test_unbound_or_unreadable_evidence_is_refused(tmp_path, field, value):
    path, rows = sources(tmp_path)
    rows[0][field] = value
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError):
        module().build_context(path, ["paper-a"], 1000)


def test_budget_never_silently_truncates_a_quote(tmp_path):
    path, _ = sources(tmp_path)
    result = module().build_context(path, ["paper-a"], 12)
    assert result["ok"] is False
    assert len(result["context"]) <= 12
    assert result["selected"] == []
    assert result["omitted"] == [{"id": "paper-a", "reason": "context_budget"}]


def test_duplicate_ids_and_missing_requested_id_are_refused(tmp_path):
    path, rows = sources(tmp_path)
    path.write_text(json.dumps(rows + rows), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        module().build_context(path, ["paper-a"], 1000)
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        module().build_context(path, ["missing"], 1000)


def test_abstract_access_stays_visible_and_repeated_quote_is_deduplicated(tmp_path):
    path, rows = sources(tmp_path)
    rows[0]["access"] = "abstract"
    rows.append({**rows[0], "id": "paper-b"})
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = module().build_context(path, ["paper-a", "paper-b"], 1000)
    assert "access=abstract" in result["context"]
    assert result["selected"] == ["paper-a"]
    assert result["duplicates"] == [{"id": "paper-b", "duplicate_of": "paper-a"}]


@pytest.mark.parametrize("identifier", [" paper-a ", "paper]a", "paper[a", "paper\na", "paper\ta"])
def test_ids_that_cannot_round_trip_through_citation_markers_are_refused(tmp_path, identifier):
    path, rows = sources(tmp_path)
    rows[0]["id"] = identifier
    path.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError, match="id"):
        module().build_context(path, [identifier], 1000)


def test_valid_unicode_id_round_trips_through_existing_citation_guard(tmp_path):
    path, rows = sources(tmp_path)
    rows[0]["id"] = "论文-a"
    path.write_text(json.dumps(rows), encoding="utf-8")
    result = module().build_context(path, ["论文-a"], 1000)
    spec = importlib.util.spec_from_file_location(
        "context_roundtrip_guard", SCRIPT.with_name("claim_citation_guard.py"))
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    assert guard.CITE.findall(result["context"]) == ["论文-a"]
    assert guard.check_sources(result["context"], path) is None
