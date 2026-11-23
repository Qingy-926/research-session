"""The rules digest must cover every file that changes review criteria or continuation."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "research-session"
sys.path.insert(0, str(SKILL / "scripts"))
import rule_sources

REQUIRED_CRITERIA = {
    "references/paper-audit.md",
    "references/manuscript-writing.md",
}


def unbound(required, files):
    return sorted(set(required) - set(files))


def test_required_criteria_files_are_bound():
    assert unbound(REQUIRED_CRITERIA, rule_sources.RULE_FILES) == []


def test_removing_a_required_file_is_detected():
    reduced = tuple(name for name in rule_sources.RULE_FILES
                    if name != "references/paper-audit.md")
    assert unbound(REQUIRED_CRITERIA, reduced) == ["references/paper-audit.md"]


def test_criteria_references_exist_in_the_tree():
    for name in REQUIRED_CRITERIA:
        assert (SKILL / name).is_file(), name
    independent = (SKILL / "references/independent-review.md").read_text(encoding="utf-8")
    manuscript = (SKILL / "references/manuscript-writing.md").read_text(encoding="utf-8")
    assert "paper-audit.md" in independent
    assert "independent-review.md" in manuscript and "point 3" in manuscript


def test_every_bound_file_exists():
    for name in rule_sources.RULE_FILES:
        assert (SKILL / name).is_file(), name
