import datetime as dt
import json
from pathlib import Path

import pytest

from test_reviewed_fixture_candidate import case, digest, ledger
from review_task import request_count, timestamp


@pytest.mark.parametrize("missing", [True, False])
def test_history_registered_before_task_needs_no_payload_timestamp(tmp_path, missing):
    scope, home, fixture, _, _, _, _ = case(tmp_path)
    entries = ledger.read_entries(str(home))
    start = timestamp(entries[-1]["created_at"]) + dt.timedelta(seconds=1)
    if missing:
        fixture.unlink()
    assert request_count(entries, scope, start) == 0


def test_current_review_history_still_blocks_on_expired_fixture_proof(tmp_path):
    scope, home, fixture, _, row, attestation, _ = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    entries = ledger.read_entries(str(home))
    start = timestamp(entries[-1]["created_at"]) + dt.timedelta(seconds=1)
    fixture.unlink()
    result = ledger.claim_send(str(home), scope,
                               {"review_id": row["review_id"], "point": 1, "pack_sha256": "abc"},
                               str(tmp_path / "pack.md"), 1)
    assert result["claimed"] is False
    assert len(result["summary"]["invalid_fixture_corrections"]) == 1
    assert request_count(entries, scope, start) == 0


@pytest.mark.parametrize("proof", ["valid", "missing", "revoked"])
def test_task_window_requires_effective_fixture_exclusion(tmp_path, proof):
    scope, home, _, evidence, row, attestation, _ = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    if proof == "missing":
        evidence.unlink()
    elif proof == "revoked":
        ledger.revoke_false_receipt_resolution(str(home), row["entry_id"], "test revocation")
    entries = ledger.read_entries(str(home))
    start = timestamp(row["created_at"]) - dt.timedelta(seconds=1)
    if proof == "valid":
        assert request_count(entries, scope, start) == 0
    else:
        with pytest.raises(ValueError, match="task has unresolved send history"):
            request_count(entries, scope, start)


@pytest.mark.parametrize("seconds,expected", [(-1, 0), (0, 1), (1, 1)])
def test_legacy_send_uses_receipt_time_when_registered_during_task(tmp_path, seconds, expected):
    scope, home, _, _, _, _, _ = case(tmp_path)
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
    receipt = Path(scope["canonical"]) / "legacy.review.json"
    receipt.write_text(json.dumps({
        "binding": {"review_id": "legacy", "point": 1},
        "created_at": (start + dt.timedelta(seconds=seconds)).isoformat(),
    }), encoding="utf8")
    ledger.scan_and_register(scope["project_root"], scope["project_id"], scope["canonical"], str(home))
    entries = [row for row in ledger.read_entries(str(home)) if row.get("review_id") != "review"]
    assert request_count(entries, scope, start) == expected


def test_unverified_receipt_with_unknown_time_does_not_free_budget(tmp_path):
    scope, home, _, _, _, _, _ = case(tmp_path)
    start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=5)
    receipt = Path(scope["canonical"]) / "legacy.review.json"
    receipt.write_text(json.dumps({"binding": {"review_id": "legacy", "point": 1}}), encoding="utf8")
    ledger.scan_and_register(scope["project_root"], scope["project_id"], scope["canonical"], str(home))
    entries = [row for row in ledger.read_entries(str(home)) if row.get("review_id") != "review"]
    with pytest.raises(ValueError, match="cannot determine request time"):
        request_count(entries, scope, start)
