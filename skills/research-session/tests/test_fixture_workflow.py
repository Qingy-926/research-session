import json
import pathlib
import subprocess
import sys

import pytest

from test_reviewed_fixture_candidate import case, digest, status
import review_ledger as ledger

SCRIPT = pathlib.Path(ledger.__file__).with_name("audit_dispatch.py")


def cli(home, *args):
    result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--ledger-dir", str(home), *args],
                            capture_output=True, text=True, encoding="utf8", timeout=15)
    return result, json.loads(result.stdout) if result.stdout.strip() else {}


def test_cli_template_preview_apply_and_revoke(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    before = (home / "ledger.jsonl").read_bytes()
    result, payload = cli(home, "--fixture-template", row["entry_id"])
    assert result.returncode == 0, result.stderr
    assert payload["template"]["target"]["entry_id"] == row["entry_id"]
    assert payload["template"]["decision"] is None
    args = ["--resolve-fixture", row["entry_id"], "--fixture-attestation", str(attestation),
            "--fixture-sha256", digest(attestation)]
    result, payload = cli(home, *args, "--dry-run")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert payload["applied"] is False
    assert (home / "ledger.jsonl").read_bytes() == before
    result, payload = cli(home, *args)
    assert result.returncode == 2
    assert (home / "ledger.jsonl").read_bytes() == before
    result, payload = cli(home, *args, "--confirm-independent-review")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert payload["applied"] is True
    assert status(scope, home) is None
    assert (home / "ledger.jsonl").read_bytes().startswith(before)
    result, payload = cli(home, "--revoke-fixture", row["entry_id"],
                          "--ledger-note", "independent judgment withdrawn")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert status(scope, home) is not None


def test_reapplying_different_attestation_is_not_silently_accepted(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    new = tmp_path / "another-review.json"
    doc["basis"] = "A changed judgment must be explicitly revoked and replaced"
    new.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError):
        ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(new), digest(new))


def test_attestation_target_point_must_not_accept_bool(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    doc["target"]["point"] = True
    attestation.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError):
        ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))


def test_repeated_revoke_is_idempotent(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    first = ledger.revoke_false_receipt_resolution(str(home), row["entry_id"], "withdraw")
    before = (home / "ledger.jsonl").read_bytes()
    second = ledger.revoke_false_receipt_resolution(str(home), row["entry_id"], "withdraw")
    assert first["entry_id"] == second["entry_id"]
    assert (home / "ledger.jsonl").read_bytes() == before


def test_preview_does_not_create_missing_ledger(tmp_path):
    missing = tmp_path / "not-created"
    result, payload = cli(missing, "--resolve-fixture", "unknown", "--fixture-attestation",
                          str(tmp_path / "missing.json"), "--fixture-sha256", "0" * 64, "--dry-run")
    assert result.returncode == 2
    assert not missing.exists()


def test_independent_output_missing_or_self_reference_is_rejected(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    doc.pop("review_record")
    attestation.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError):
        ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    doc["review_record"] = {"path": str(fixture), "sha256": digest(fixture)}
    attestation.write_text(json.dumps(doc), encoding="utf8")
    with pytest.raises(ValueError):
        ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))


def test_changing_independent_output_restores_block(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    pathlib.Path(doc["review_record"]["path"]).write_text("new judgment", encoding="utf8")
    assert status(scope, home) is not None


@pytest.mark.parametrize("entrypoint", ["claim", "preflight"])
def test_known_project_copy_is_refreshed_before_use(tmp_path, entrypoint):
    project_a = tmp_path / "project-991-a"
    project_b = tmp_path / "project-991-b"
    home = tmp_path / "ledger"
    for project in (project_a, project_b):
        canonical = project / "01-资料" / "reviews"
        canonical.mkdir(parents=True)
        ledger.scan_and_register(str(project), ledger.project_identity(str(project)), str(canonical), str(home))
    outside = project_b / "new" / "real.review.json"
    outside.parent.mkdir()
    outside.write_text(json.dumps({"binding": {"review_id": "review", "point": 1},
                                   "round": 2}), encoding="utf8")
    scope = {"project_id": ledger.project_identity(str(project_a)), "project_root": str(project_a),
             "canonical": str(project_a / "01-资料" / "reviews")}
    if entrypoint == "claim":
        result = ledger.claim_send(str(home), scope,
                                   {"review_id": "review", "point": 1, "pack_sha256": "abc"},
                                   str(project_a / "pack.md"), 1)
        assert result["claimed"] is False
        summary = result["summary"]
    else:
        summary = ledger.preflight(str(home), scope, "review", 1)
    assert ledger.blocking_reason(summary) is not None
    assert any(ledger.norm_path(x.get("receipt", "")) == ledger.norm_path(str(outside))
               for x in summary["outside_receipts"])


def test_missing_registered_copy_blocks_instead_of_being_forgotten(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    copy = pathlib.Path(scope["project_root"]).with_name("project-991-copy")
    (copy / "01-资料" / "reviews").mkdir(parents=True)
    ledger.scan_and_register(str(copy), scope["project_id"], ledger.canonical_reviews(str(copy)), str(home))
    copy.rename(copy.with_name("moved-project-copy"))
    result = ledger.claim_send(str(home), scope,
                               {"review_id": "review", "point": 1, "pack_sha256": "abc"},
                               str(tmp_path / "pack.md"), 1)
    assert result["claimed"] is False
    assert result["summary"]["incomplete_roots"]


@pytest.mark.skipif(ledger.os.name != "nt", reason="Windows kernel ledger lease path only")
def test_crashed_ledger_owner_releases_mutex_without_changing_history(tmp_path):
    import time
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    ready = tmp_path / "ready"
    code = ("import sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "import review_ledger as l; l._acquire(sys.argv[2]); "
            "Path(sys.argv[3]).write_text('ready'); time.sleep(30)")
    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(pathlib.Path(ledger.__file__).parent),
                                ledger.ledger_path(str(home)), str(ready)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    lock = pathlib.Path(ledger.ledger_path(str(home)) + ".lock")
    before = (home / "ledger.jsonl").read_bytes()
    mutex = ledger._acquire(ledger.ledger_path(str(home)), timeout=.2)
    ledger._release_ledger(mutex)
    assert not lock.exists()
    assert (home / "ledger.jsonl").read_bytes() == before
    assert process.poll() is not None
    result = ledger.claim_send(str(home), scope,
                               {"review_id": "review", "point": 1, "pack_sha256": "abc"},
                               str(tmp_path / "pack.md"), 1)
    assert result["claimed"] is True


@pytest.mark.skipif(ledger.os.name == "nt", reason="POSIX ledger mutex is an O_EXCL sentinel")
def test_posix_crashed_ledger_owner_keeps_mutex_for_manual_clear(tmp_path):
    import time
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    ready = tmp_path / "ready"
    code = ("import sys,time; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "import review_ledger as l; l._acquire(sys.argv[2]); "
            "Path(sys.argv[3]).write_text('ready'); time.sleep(30)")
    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(pathlib.Path(ledger.__file__).parent),
                                ledger.ledger_path(str(home)), str(ready)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    lock = pathlib.Path(ledger.ledger_path(str(home)) + ".lock")
    before = (home / "ledger.jsonl").read_bytes()
    with pytest.raises(ValueError, match="核查后重试"):
        ledger._acquire(ledger.ledger_path(str(home)), timeout=.2)
    assert lock.exists()
    assert (home / "ledger.jsonl").read_bytes() == before
    lock.unlink()
    mutex = ledger._acquire(ledger.ledger_path(str(home)), timeout=.2)
    ledger._release_ledger(mutex)


def test_crashed_send_intent_needs_explicit_resolution(tmp_path):
    scope, home, fixture, evidence, row, attestation, doc = case(tmp_path)
    ledger.resolve_reviewed_fixture(str(home), row["entry_id"], str(attestation), digest(attestation))
    binding = {"review_id": "review", "point": 1, "pack_sha256": "abc"}
    first = ledger.claim_send(str(home), scope, binding, str(tmp_path / "pack.md"), 1)
    assert first["claimed"]
    second = ledger.claim_send(str(home), scope, binding, str(tmp_path / "pack.md"), 1)
    assert not second["claimed"]
    ledger.resolve_intent(str(home), first["entry"]["entry_id"], "verified fixture request was never sent")
    assert ledger.claim_send(str(home), scope, binding, str(tmp_path / "pack.md"), 1)["claimed"]


def test_old_and_new_clients_share_claim_lock(tmp_path):
    project = tmp_path / "project-991"
    canonical = project / "01-资料" / "reviews"
    canonical.mkdir(parents=True)
    home = tmp_path / "ledger"
    scope = {"project_id": ledger.project_identity(str(project)), "project_root": str(project),
             "canonical": str(canonical)}
    ledger.scan_and_register(str(project), scope["project_id"], str(canonical), str(home))
    baseline = pathlib.Path(__file__).parent / "fixtures" / "review_ledger_v218.py"
    assert digest(baseline) == "df29491b7e7ece09b021ddb681d8fc518bd6bbc0a526d1fc3da1d03388cef524"
    script = ("import sys,json,importlib.util; "
              "s=importlib.util.spec_from_file_location('client',sys.argv[1]); "
              "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
              "r=m.claim_send(sys.argv[2],json.loads(sys.argv[3]),"
              "{'review_id':'review','point':1,'pack_sha256':'abc'},sys.argv[4],1); "
              "print(json.dumps(r['claimed']))")
    processes = [subprocess.Popen([sys.executable, "-B", "-c", script, str(client), str(home),
                                   json.dumps(scope), str(tmp_path / "pack.md")],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf8")
                 for client in (pathlib.Path(ledger.__file__), baseline)]
    try:
        outputs = [p.communicate(timeout=15) for p in processes]
        assert all(p.returncode == 0 for p in processes), outputs
        assert sorted(json.loads(out) for out, _ in outputs) == [False, True]
        assert sum(x["kind"] == "send_intent" for x in ledger.read_entries(str(home))) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


@pytest.mark.skipif(ledger.os.name != "nt", reason="Windows kernel ledger lease path only")
def test_lease_inspection_survives_a_sentinel_released_between_open_and_lstat(tmp_path, monkeypatch):
    path = str(tmp_path / "ledger.jsonl")
    lock = path + ".lock"
    pathlib.Path(lock).write_text('{"pid": 999999, "created_at": "2000-01-01T00:00:00+00:00"}',
                                  encoding="utf-8")
    original = ledger._ordinary_unlinked_file
    calls = []

    def vanish_once(target):
        calls.append(target)
        if len(calls) == 1:
            pathlib.Path(target).unlink()
            raise FileNotFoundError(2, "released between open and inspection", target)
        return original(target)

    monkeypatch.setattr(ledger, "_ordinary_unlinked_file", vanish_once)
    assert ledger._acquire(path) == lock
    ledger._release_ledger(lock)
