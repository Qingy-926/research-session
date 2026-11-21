import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import log_governance as lg


def profile(tmp_path):
    return {
        "project_root": str(tmp_path),
        "current_log_dirs": ["logs"],
        "historical_log_dirs": ["old"],
        "archive_root": "archive",
        "protected_globs": ["*.pt", "*.json"],
        "activity_process_matches": ["train_crossrec_honest"],
        "cold_archive_days": 30,
    }


def test_rewrite_backs_up_and_preserves_source_path(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    src = tmp_path / "logs" / "bad.log"
    bad = "诚实".encode("utf-8").decode("latin1") + " epoch 1 loss 0.2"
    src.write_bytes(bad.encode("utf-8"))
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    result = lg.rewrite_logs(profile(tmp_path))
    assert result["rewritten"] == 1
    assert src.read_text(encoding="utf-8").find("epoch 1") >= 0
    backups = list((tmp_path / "archive").rglob("bad.log"))
    assert backups and backups[0].read_bytes() != src.read_bytes()
    manifest = list((tmp_path / "archive").rglob("manifest.json"))
    assert manifest


def test_rewrite_refuses_active_process(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(lg, "find_activity", lambda *_: [{"pid": 1}])
    try:
        lg.rewrite_logs(profile(tmp_path))
    except lg.ActivityError:
        pass
    else:
        raise AssertionError("rewrite must refuse active processes")


def test_archive_dry_run_does_not_move_protected_or_overwrite(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run.log").write_text("epoch 1", encoding="utf-8")
    (tmp_path / "logs" / "model.pt").write_bytes(b"x")
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    plan = lg.archive_logs(profile(tmp_path), apply=False)
    assert plan["apply"] is False
    assert (tmp_path / "logs" / "run.log").exists()
    assert not (tmp_path / "archive" / "01-运行记录" / "run.log").exists()


def test_audit_flags_mojibake_path_component(tmp_path, monkeypatch):
    bad_dir = tmp_path / "05-鏃ュ織"
    bad_dir.mkdir()
    (bad_dir / "run.log").write_text("epoch 1", encoding="utf-8")
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    result = lg.audit_logs({
        **profile(tmp_path),
        "historical_log_dirs": ["05-鏃ュ織"],
    })
    assert any(row["kind"] == "directory" and "鏃" in row["path"]
               for row in result["suspicious_paths"])


def test_archive_rejects_mojibake_archive_root(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    bad_profile = {**profile(tmp_path), "archive_root": "99-鏃ュ織"}
    try:
        lg.archive_logs(bad_profile, apply=False)
    except ValueError:
        pass
    else:
        raise AssertionError("archive must reject mojibake destination paths")


def test_archive_preserves_model_even_without_protected_globs(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    model = tmp_path / "logs/model.pt"; model.write_bytes(b"model")
    result = tmp_path / "logs/result.json"; result.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    lg.archive_logs({**profile(tmp_path), "protected_globs": []}, apply=True)
    assert model.exists() and result.exists()


def test_archive_collision_checked_before_any_move(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/a.log").write_text("a", encoding="utf-8")
    (tmp_path / "logs/b.log").write_text("b", encoding="utf-8")
    target = tmp_path / "archive/01-运行记录"
    target.mkdir(parents=True)
    (target / "b.log").write_text("old", encoding="utf-8")
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    import pytest
    with pytest.raises(FileExistsError): lg.archive_logs(profile(tmp_path), apply=True)
    assert (tmp_path / "logs/a.log").exists()


def test_log_paths_cannot_escape(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(lg, "find_activity", lambda *_: [])
    with pytest.raises(ValueError): lg.archive_logs({**profile(tmp_path), "archive_root": "../outside"}, apply=True)


def test_missing_activity_scope_fails_closed(tmp_path):
    import pytest
    with pytest.raises(lg.ActivityError): lg.find_activity({"project_root": str(tmp_path)})
