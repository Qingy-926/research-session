"""Single source of truth for the files bound into the review rules digest.

Every file that can change a review criterion, a receipt's continuation
meaning, or the dispatch/ledger behavior that interprets them belongs here.
Adding or removing an entry changes ``rules_sha256`` and therefore invalidates
continuation until the change is registered in
``references/review-rules-compatibility.json``.
"""

from __future__ import annotations

import hashlib
import os

RULE_FILES = (
    "scripts/audit_dispatch.py",
    "scripts/review_evidence.py",
    "scripts/review_ledger.py",
    "scripts/review_format.py",
    "scripts/review_task.py",
    "scripts/rule_sources.py",
    "SKILL.md",
    "references/codex-review-pack.md",
    "references/autonomy-bounds.md",
    "references/review-evidence.md",
    "references/project-work.md",
    "references/preregistration.md",
    "references/independent-review.md",
    "references/autonomous-start.md",
    "references/retrieval-evidence.md",
    "references/claim-evidence.md",
    "references/paper-audit.md",
    "references/manuscript-writing.md",
)


def _normalized(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))


def paths(root: str, reference: str | None = None) -> list[str]:
    base = _normalized(root)
    result = [_normalized(os.path.join(base, name)) for name in RULE_FILES]
    if reference:
        candidate = _normalized(reference)
        if candidate not in result:
            result.append(candidate)
    return result


def digest(root: str, reference: str | None = None) -> str:
    value = hashlib.sha256()
    for path in paths(root, reference):
        with open(path, "rb") as handle:
            value.update(handle.read())
    return value.hexdigest()
