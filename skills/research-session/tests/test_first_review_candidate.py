import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import audit_dispatch as audit


def response(target):
    return {
        "decision": "待整改",
        "summary": "Two independently correctable display errors.",
        "checklist": [{"item": 1, "status": "存疑", "reason": "report.md versus frozen counts",
                       "finding_ids": ["A", "B"]}],
        "claim_checks": [{"item": 1, "status": "存疑", "reason": "report.md row 1 versus counts.json /a",
                          "finding_ids": ["A"]}],
        "findings": [
            {"id": key, "kind": "blocking", "domain": "wording", "location": "report.md row " + key,
             "evidence": "counts.json /" + key, "impact": "Incorrect display",
             "change": "Replace " + old + " with " + new, "acceptance": "Exact replacement with counts unchanged",
             "closure": {"mode": "local", "edits": [{"path": str(target), "before": old, "after": new}]}}
            for key, old, new in (("A", "A 96%", "A 94%"), ("B", "B 71%", "B 75%"))
        ],
        "closed_findings": [],
    }


def scope(path):
    return {"claims": ["report.md row 1 matches frozen counts"], "editable_reports": [str(path)]}


@pytest.mark.parametrize("defect", ["empty_reason", "orphan", "missing_claim", "missing_triage", "gap_as_repair"])
def test_incomplete_first_review_is_rejected(tmp_path, defect):
    target = tmp_path / "report.md"
    data = response(target)
    if defect == "empty_reason":
        data["checklist"][0].update(status="通过", reason="", finding_ids=[])
    elif defect == "orphan":
        data["checklist"][0]["finding_ids"] = ["A"]
    elif defect == "missing_claim":
        data["claim_checks"] = []
    elif defect == "missing_triage":
        del data["findings"][1]["closure"]
    elif defect == "gap_as_repair":
        data["checklist"][0]["missing_evidence"] = "counts.json /run_id"
    with pytest.raises(ValueError):
        audit.parse_review(json.dumps(data), 1, first_review=scope(target))


def test_mixed_blockers_identify_exact_items_for_followup(tmp_path):
    target = tmp_path / "report.md"
    data = response(target)
    data["findings"][1]["closure"] = {"mode": "recheck", "reason": "Reassess the denominator"}
    report = audit.parse_review(json.dumps(data), 1, first_review=scope(target))
    result, code = audit.review_result({"round": 1, "report": report, "binding": {"first_review": scope(target)}}, "old.json")
    assert (code, result["gate_passed"], result["disposition"], result["recheck_ids"]) == (3, False, "recheck_required", ["B"])


def test_missing_sources_are_listed_without_discarding_known_errors(tmp_path):
    target = tmp_path / "report.md"
    data = response(target)
    data["decision"] = "未完成"
    data["checklist"][0]["missing_evidence"] = ["counts.json /run_id", "review-history.md L8"]
    report = audit.parse_review(json.dumps(data), 1, first_review=scope(target))
    result, code = audit.review_result({"round": 1, "report": report, "binding": {"first_review": scope(target)}}, "old.json")
    assert [finding["id"] for finding in report["findings"]] == ["A", "B"]
    assert (code, result["gate_passed"], result["disposition"]) == (2, False, "incomplete_stop")
    data["checklist"][0]["missing_evidence"] = ["counts.json /run_id", ""]
    with pytest.raises(ValueError):
        audit.parse_review(json.dumps(data), 1, first_review=scope(target))


def test_local_closure_rechecks_every_edit_and_all_other_bound_files(tmp_path):
    target = tmp_path / "report.md"
    counts = tmp_path / "counts.json"
    target.write_bytes(b"A 96%\nB 71%\n")
    counts.write_bytes(b"original")
    binding = {"first_review": scope(target), "point": 3,
               "files": [{"path": str(path), "sha256": audit.sha256_hex(path.read_bytes())} for path in (target, counts)]}
    report = audit.parse_review(json.dumps(response(target)), 1, first_review=scope(target))
    receipt = {"round": 1, "report": report, "binding": binding}
    pending, code = audit.review_result(receipt, "old.json")
    assert (code, pending["gate_passed"], pending["disposition"]) == (3, False, "local_verification_required")
    for changes in (b"A 94%\nB 71%\n", b"A 94%\nB 75%\nExtra claim\n"):
        target.write_bytes(changes)
        with pytest.raises(ValueError):
            audit.verify_local_edits(receipt)
    target.write_bytes(b"A 94%\nB 75%\n")
    assert audit.verify_local_edits(receipt) == [{"path": str(target), "sha256": audit.sha256_hex(target.read_bytes())}]
    counts.write_bytes(b"drift")
    with pytest.raises(ValueError):
        audit.verify_local_edits(receipt)


def test_chained_edits_cannot_close_an_unchanged_report(tmp_path):
    target = tmp_path / "report.md"
    target.write_bytes(b"A 96%\n")
    data = response(target)
    data["findings"][1]["closure"]["edits"][0].update(before="A 94%", after="A 96%")
    binding = {"first_review": scope(target), "point": 3,
               "files": [{"path": str(target), "sha256": audit.sha256_hex(target.read_bytes())}]}
    receipt = {"round": 1, "report": data, "binding": binding}
    with pytest.raises(ValueError):
        audit.verify_local_edits(receipt)


def test_local_verification_uses_real_first_receipt_without_a_second_request(tmp_path, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from test_review_closure import Proxy

    reviews = tmp_path / "project-991-fixture" / "01-资料" / "reviews"
    reviews.mkdir(parents=True)
    target = reviews / "report.md"
    target.write_bytes(b"A 96%\nB 71%\n")
    pack = reviews / "package.md"
    pack.write_text("Synthetic point-3 evidence.", encoding="utf-8")
    manifest = reviews / "inputs.json"
    manifest.write_text(json.dumps({"review_id": "point3-demo", "first_review": scope(target),
                                    "files": [{"path": str(target), "sha256": audit.sha256_hex(target.read_bytes())}]}),
                        encoding="utf-8")
    data = response(target)
    _, checks = audit.load_checklist(3, audit.REFERENCE_MD)
    data["checklist"].extend(
        {"item": i, "status": "不适用", "reason": "Synthetic coverage only", "finding_ids": []}
        for i in range(2, len(checks) + 1)
    )
    monkeypatch.setattr(Proxy, "response", data)
    monkeypatch.setattr(Proxy, "requests", [])
    monkeypatch.setenv("RESEARCH_SESSION_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.delenv("RESEARCH_SESSION_PROVIDER_CONFIG", raising=False)
    monkeypatch.delenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", raising=False)

    def run(*args):
        proc = subprocess.run([sys.executable, "-B", audit.__file__, *map(str, args)],
                              capture_output=True, encoding="utf-8", timeout=15)
        return proc.returncode, json.loads(proc.stdout)

    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            code, first = run("--pack", pack, "--point", 3, "--inputs", manifest,
                              "--responses-url", f"http://127.0.0.1:{server.server_port}/v1/responses",
                              "--model", "fixture-reviewer", "--no-stream")
            assert (code, first.get("disposition")) == (3, "local_verification_required"), first
            receipt = pathlib.Path(first["receipt"])
            original_receipt = receipt.read_bytes()
            verify = ("--verify-local-edits", receipt, "--expected-receipt-sha256", first["receipt_sha256"])
            run_manifests = tmp_path / "must-not-write"
            monkeypatch.setenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", str(run_manifests))
            assert run(*verify)[0] == 2
            assert run(*verify, "--pack", pack, "--point", 3, "--inputs", manifest)[0] == 2
            target.write_bytes(b"A 94%\nB 75%\n")
            receipt.write_bytes(original_receipt + b" ")
            assert run(*verify)[0] == 2
            receipt.write_bytes(original_receipt)
            code, final = run(*verify)
            assert (code, final["local_release_ready"], final["gate_passed"]) == (0, True, False)
            assert final["decision"] == "待整改"
            assert receipt.read_bytes() == original_receipt
            assert len(Proxy.requests) == 1
            assert not run_manifests.exists()
        finally:
            server.shutdown()
            thread.join()
