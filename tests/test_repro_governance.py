"""Behavioral contract: no training, no project writes during audits."""
import copy
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from repro_governance import (audit_metrics, audit_paper, freeze_protocol, init_profile,
                              preflight, verify_matrix, verdict)


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def run_git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("/evidence/\n", encoding="utf-8")
    (root / "paper.txt").write_text("Accuracy equals correct predictions divided by trials.\n", encoding="utf-8")
    (root / "train.py").write_text("loss = cross_entropy(logits, labels)\n", encoding="utf-8")
    (root / "data.bin").write_bytes(b"frozen-data")
    protocol = {
        "id": "fixture-v1", "stage": "formal", "methods": ["baseline", "contrastive"],
        "random_axes": {"training_seed": [23, 31], "subject_seed": [101, 202], "beat_seed": [1001]},
        "assignment_axes": {"cohort": "subject_seed", "samples": "beat_seed", "training": "training_seed"},
        "excluded_subjects": ["X"], "required_roles": ["enrollment", "genuine", "impostor"],
        "disjoint_subject_roles": [["genuine", "impostor"]],
        "disjoint_sample_roles": [["enrollment", "genuine"]],
        "expected_cohort": ["A", "B", "C"], "dataset_hash": digest(root / "data.bin"),
        "metric_definitions": {"accuracy": {"adapter": "count_rate", "unit": "percent", "definition": "correct / trials", "numerator_key": "correct", "denominator_key": "trials"}},
        "gates": [{"id": "accuracy-gate", "metric": "accuracy", "method": "baseline", "split": "test",
                   "selection_semantics": "validation_fixed", "scope": "full", "score_semantics": "classifier",
                   "aggregate": "mean", "operator": ">=", "threshold": 90.0, "purpose": "formal"}],
    }
    save(root / "protocol.json", protocol)
    run_git(root, "init")
    run_git(root, "config", "user.email", "fixture@example.invalid")
    run_git(root, "config", "user.name", "Fixture")
    run_git(root, "add", ".")
    run_git(root, "commit", "-m", "source fixture")
    commit = run_git(root, "rev-parse", "HEAD")
    evidence = root / "evidence"
    save(evidence / "paper-audit.json", {"items": [{"id": "metric", "source_file": "paper.txt", "source_sha256": digest(root / "paper.txt"),
        "locator": "line 1", "quote": "correct predictions divided by trials", "implementation_file": "train.py",
        "implementation_sha256": digest(root / "train.py"), "implementation_locator": "line 1", "interpretation": "count-based accuracy", "review_status": "reviewed"}]})
    (evidence / "tests.log").write_text("fixture evidence only; not a real training run\n", encoding="utf-8")
    save(evidence / "tests.json", {"exit_code": 0, "command": "fixture unit test", "commit": commit,
         "log_file": "evidence/tests.log", "log_sha256": digest(evidence / "tests.log")})
    profile = {"schema": "rigorous-paper-reproduction/v1", "project_root": str(root),
        "paper": {"title": "Fixture", "evidence_file": "evidence/paper-audit.json", "required_items": ["metric"]},
        "code": {"canonical_commit": commit, "require_clean_git": True, "required_files": [{"path": "train.py", "sha256": digest(root / "train.py")}]},
        "data": {"required_files": [{"path": "data.bin", "sha256": digest(root / "data.bin")}], "excluded_subjects": ["X"]},
        "protocol": {**protocol, "file": "protocol.json", "sha256": digest(root / "protocol.json")},
        "tests": {"evidence_file": "evidence/tests.json", "required_exit_code": 0},
        "resources": {"minimum_free_gb": 0.0}, "matrix_file": "evidence/matrix.json", "metrics_file": "evidence/metrics.json"}
    rows = []
    for method, ts, ss, bs in itertools.product(protocol["methods"], [23, 31], [101, 202], [1001]):
        who, imp = (["A", "B"], ["C"]) if ss == 101 else (["A", "C"], ["B"])
        assignment = {"roles": {"enrollment": who, "genuine": who, "impostor": imp}, "samples": {}}
        for role, people in assignment["roles"].items():
            assignment["samples"][role] = [{"sample_id": f"{x}-{role}-{bs}", "subject_id": x, "recording_id": f"{x}-{role}"} for x in people]
        ap = save(evidence / f"assignment-{ss}-{bs}.json", assignment)
        rows.append({"method": method, "training_seed": ts, "subject_seed": ss, "beat_seed": bs,
                     "assignment_file": str(ap.relative_to(root)), "assignment_sha256": digest(ap), "status": "completed"})
    metrics = []
    for i, row in enumerate(rows):
        source = save(evidence / f"result-{i}.json", {"correct": 95, "trials": 100})
        metrics.append({**{k: row[k] for k in ("method", "training_seed", "subject_seed", "beat_seed")},
            "dataset_hash": protocol["dataset_hash"], "protocol_hash": profile["protocol"]["sha256"], "code_commit": commit,
            "metric": "accuracy", "split": "test", "selection_semantics": "validation_fixed", "scope": "full", "score_semantics": "classifier",
            "purpose": "formal", "unit": "percent", "value": 95.0, "adapter": "count_rate", "numerator_key": "correct", "denominator_key": "trials",
            "assignment_sha256": row["assignment_sha256"], "source_json": str(source.relative_to(root)), "source_sha256": digest(source), "participates_in_gate": row["method"] == "baseline"})
    for metric in metrics:
        source_path = root / metric["source_json"]
        raw = json.loads(source_path.read_text())
        raw["context"] = {k: metric[k] for k in ("metric", "method", "split", "selection_semantics", "scope", "score_semantics", "purpose", "dataset_hash", "protocol_hash", "code_commit", "assignment_sha256", "training_seed", "subject_seed", "beat_seed")}
        save(source_path, raw)
        metric["source_sha256"] = digest(source_path)
    save(evidence / "matrix.json", rows)
    save(evidence / "metrics.json", metrics)
    path = save(tmp_path / "profile.json", profile)  # outside Git: no self-referential commit
    return root, path, profile, rows, metrics


def checks(report):
    return {x["check"] for x in report["failures"]}


def test_preflight_clean_bound(case):
    assert preflight(case[1])["passed"]


def test_dirty_git(case):
    (case[0] / "untracked.py").write_text("dirty", encoding="utf-8")
    assert "git_clean" in checks(preflight(case[1]))


def test_changed_data(case):
    (case[0] / "data.bin").write_bytes(b"changed")
    assert "file_sha256" in checks(preflight(case[1]))


def test_stale_test_evidence(case):
    p = case[0] / "evidence/tests.json"
    d = json.loads(p.read_text()); d["commit"] = "0" * 40; save(p, d)
    assert "tests_commit" in checks(preflight(case[1]))


def test_empty_profile_blocked(tmp_path):
    assert not preflight(save(tmp_path / "p.json", {}))["passed"]


def test_path_escape(case):
    p = copy.deepcopy(case[2]); p["code"]["required_files"][0]["path"] = "../outside"
    assert not preflight(save(case[1], p))["passed"]


def test_missing_source_not_verified(case):
    p = case[0] / "evidence/paper-audit.json"
    d = json.loads(p.read_text()); d["items"][0]["quote"] = "not in the paper"; save(p, d)
    assert not audit_paper(case[2], case[0])["passed"]


def test_unreviewed_paper_blocked(case):
    p = case[0] / "evidence/paper-audit.json"
    d = json.loads(p.read_text()); d["items"][0]["review_status"] = "unresolved"; save(p, d)
    assert not preflight(case[1])["passed"]


def test_protocol_declaration_drift(case):
    p = copy.deepcopy(case[2]); p["protocol"]["excluded_subjects"] = []
    assert "protocol_alignment" in checks(preflight(save(case[1], p)))


def test_matrix_complete(case):
    r = verify_matrix(case[2], case[3], case[0])
    assert r["passed"], r
    assert r["expected_runs"] == 8


def test_matrix_missing(case):
    assert "matrix_completeness" in checks(verify_matrix(case[2], case[3][:-1], case[0]))


def test_matrix_duplicate(case):
    assert not verify_matrix(case[2], case[3] + [case[3][0]], case[0])["passed"]


def test_collapsed_subject_seeds(case):
    rows = copy.deepcopy(case[3])
    for row in rows:
        if row["subject_seed"] == 202:
            row["assignment_file"] = rows[0]["assignment_file"]; row["assignment_sha256"] = rows[0]["assignment_sha256"]
    assert "subject_seed_collapsed" in checks(verify_matrix(case[2], rows, case[0]))


def test_hash_only_assignment_rejected(case):
    rows = copy.deepcopy(case[3]); rows[0].pop("assignment_file"); rows[0]["cohort_hash"] = "trusted-claim"
    assert not verify_matrix(case[2], rows, case[0])["passed"]


def test_excluded_subject_leaks(case):
    rows = copy.deepcopy(case[3]); p = case[0] / rows[0]["assignment_file"]
    d = json.loads(p.read_text()); d["roles"]["genuine"].append("X"); save(p, d)
    rows[0]["assignment_sha256"] = digest(p)
    assert "excluded_subjects" in checks(verify_matrix(case[2], rows, case[0]))


def test_paired_samples_drift(case):
    rows = copy.deepcopy(case[3]); d = json.loads((case[0] / rows[0]["assignment_file"]).read_text())
    d["samples"]["genuine"][0]["sample_id"] = "other"
    p = save(case[0] / "evidence/changed.json", d)
    rows[-4]["assignment_file"] = "evidence/changed.json"; rows[-4]["assignment_sha256"] = digest(p)
    assert "paired_samples" in checks(verify_matrix(case[2], rows, case[0]))


def test_metric_provenance_required():
    assert "metric_provenance" in checks(audit_metrics([{"metric": "eer", "value": 4.0}]))


def test_metrics_valid(case):
    assert audit_metrics(case[4], case[2], case[0])["passed"]


def test_metrics_recompute_mismatch(case):
    rows = copy.deepcopy(case[4]); rows[0]["value"] = 99.0
    assert "metric_value" in checks(audit_metrics(rows, case[2], case[0]))


def test_oracle_not_authorized(case):
    rows = copy.deepcopy(case[4]); rows[0]["selection_semantics"] = "test_oracle"; rows[0]["preregistered"] = True
    assert "oracle_in_gate" in checks(audit_metrics(rows, case[2], case[0]))


def test_oracle_explicit_protocol_permission(case):
    rows = copy.deepcopy(case[4]); profile = copy.deepcopy(case[2])
    for row in rows:
        row["selection_semantics"] = "test_oracle"
        source_path = case[0] / row["source_json"]
        raw = json.loads(source_path.read_text()); raw["context"]["selection_semantics"] = "test_oracle"
        save(source_path, raw); row["source_sha256"] = digest(source_path)
    profile["protocol"]["gates"][0].update(selection_semantics="test_oracle", allow_test_oracle=True)
    assert audit_metrics(rows, profile, case[0])["passed"]


def test_diagnostic_gate_blocked(case):
    rows = copy.deepcopy(case[4]); rows[0]["purpose"] = "diagnostic"
    assert not audit_metrics(rows, case[2], case[0])["passed"]


def test_unsupported_metric_never_passes(case):
    rows = copy.deepcopy(case[4]); rows[0]["adapter"] = "average_classifier_error_as_eer"
    assert not audit_metrics(rows, case[2], case[0])["passed"]


def test_nan_rejected(case):
    rows = copy.deepcopy(case[4]); rows[0]["value"] = float("nan")
    assert not audit_metrics(rows, case[2], case[0])["passed"]


def test_gate_cannot_hide_failed_row(case):
    rows = copy.deepcopy(case[4]); rows[0]["participates_in_gate"] = False
    save(case[0] / "evidence/metrics.json", rows)
    r = verdict(case[1]); assert not r["passed"] and r["decision"] == "BLOCKED"


def test_verdict_pass(case):
    r = verdict(case[1]); assert r["decision"] == "CRITERIA_MET", r


def test_verdict_threshold_failure(case):
    rows = copy.deepcopy(case[4])
    for row in rows:
        p = case[0] / row["source_json"]; raw = json.loads(p.read_text()); raw["correct"] = 80; save(p, raw); row["source_sha256"] = digest(p); row["value"] = 80.0
    save(case[0] / "evidence/metrics.json", rows)
    r = verdict(case[1]); assert r["decision"] == "CRITERIA_NOT_MET", r


def test_init_incomplete_no_overwrite(tmp_path):
    path = tmp_path / "profile.json"
    init_profile(path, tmp_path)
    assert not preflight(path)["passed"]
    with pytest.raises(FileExistsError): init_profile(path, tmp_path)


def test_freeze_no_overwrite(case):
    out = case[1].parent / "frozen.json"
    freeze_protocol(case[1], out)
    with pytest.raises(FileExistsError): freeze_protocol(case[1], out)


def test_audits_are_read_only(case):
    before = {str(p): digest(p) for p in case[0].rglob("*") if p.is_file()}
    preflight(case[1]); verify_matrix(case[2], case[3], case[0]); audit_metrics(case[4], case[2], case[0]); verdict(case[1])
    after = {str(p): digest(p) for p in case[0].rglob("*") if p.is_file()}
    assert before == after


def test_metrics_reject_source_context_mismatch(case):
    rows = copy.deepcopy(case[4]); p = case[0] / rows[0]["source_json"]
    d = json.loads(p.read_text()); d["context"] = {"training_seed": "some other seed"}; save(p, d)
    rows[0]["source_sha256"] = digest(p)
    assert not audit_metrics(rows, case[2], case[0])["passed"]


def test_numerator_cannot_be_cherry_picked(case):
    rows = copy.deepcopy(case[4]); p = case[0] / rows[0]["source_json"]
    d = json.loads(p.read_text()); d["best_count"] = 99; save(p, d)
    rows[0].update(source_sha256=digest(p), numerator_key="best_count", value=99.0)
    assert not audit_metrics(rows, case[2], case[0])["passed"]


def test_result_assignment_must_match_matrix(case):
    rows = copy.deepcopy(case[4]); rows[0]["assignment_sha256"] = "0" * 64
    assert not audit_metrics(rows, case[2], case[0])["passed"]


def test_development_cannot_receive_formal_verdict(case):
    p = copy.deepcopy(case[2]); p["protocol"]["stage"] = "development"
    # The stage is frozen in this fixture; alignment must also reject the drift.
    save(case[1], p)
    assert verdict(case[1])["decision"] == "BLOCKED"


def test_malformed_rows_are_structured_failures(case):
    for rows in ([None], ["not an object"], [1]):
        assert not verify_matrix(case[2], rows, case[0])["passed"]
        assert not audit_metrics(rows, case[2], case[0])["passed"]


@pytest.mark.parametrize("x,y,expected", [([0.0, 1.0], [1.0, 0.0], 50.0), ([0.0, 0.2, 1.0], [1.0, 0.2, 0.0], 20.0)])
def test_eer_curve_crossing_arithmetic(x, y, expected):
    from repro_governance import recompute
    definition = {"adapter": "curve_crossing_linear", "unit": "percent", "false_accept_key": "fpir", "false_reject_key": "fnir_union"}
    row = {**definition, "metric": "open_identification_eer"}
    assert recompute(row, {"fpir": x, "fnir_union": y}, definition) == pytest.approx(expected)


@pytest.mark.parametrize("x,y", [([0.0, 0.1], [0.9, 0.8]), ([0.3, 0.1], [0.9, 0.0]), ([0.0, float("nan")], [1.0, 0.0])])
def test_invalid_curve_cannot_be_called_eer(x, y):
    from repro_governance import recompute
    definition = {"adapter": "curve_crossing_linear", "unit": "percent", "false_accept_key": "fpir", "false_reject_key": "fnir_union"}
    with pytest.raises(ValueError): recompute({**definition, "metric": "eer"}, {"fpir": x, "fnir_union": y}, definition)


def test_classifier_error_mean_not_eer():
    from repro_governance import recompute
    definition = {"adapter": "count_rate", "unit": "percent", "numerator_key": "errors", "denominator_key": "trials"}
    with pytest.raises(ValueError): recompute({**definition, "metric": "eer"}, {"errors": 4, "trials": 100}, definition)
