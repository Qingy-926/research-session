"""Published failure -> one format request -> one ordinary bounded recheck."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

import pytest

from test_report_format_correction import original
from test_review_closure import Proxy
import audit_dispatch as audit
import review_format


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "sha256": audit.sha256_hex(path.read_bytes())}


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    reviews = tmp_path / "project-991-fixture" / "01-资料" / "reviews"
    reviews.mkdir(parents=True)
    protocol = reviews / "protocol.md"
    protocol.write_text("frozen synthetic input", encoding="utf-8")
    pack = reviews / "first.md"
    pack.write_text("Synthetic evidence, no real research.", encoding="utf-8")
    manifest = reviews / "inputs.json"
    data = original()
    data["checklist"].extend({"item": i, "status": "不适用", "reason": "Synthetic fixture",
                              "finding_ids": []} for i in range(2, 8))
    scope = {"claims": ["Original acceptance case is closed"], "editable_reports": []}
    write(manifest, {"review_id": "format-demo", "first_review": scope,
        "files": [{"path": str(protocol), "sha256": audit.sha256_hex(protocol.read_bytes())}]})
    monkeypatch.setenv("RESEARCH_SESSION_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.delenv("RESEARCH_SESSION_PROVIDER_CONFIG", raising=False)
    monkeypatch.delenv("RESEARCH_SESSION_RUN_MANIFEST_DIR", raising=False)
    monkeypatch.setattr(Proxy, "requests", [])
    monkeypatch.setattr(Proxy, "response", data)
    monkeypatch.setattr(Proxy, "callbacks", {})
    monkeypatch.setattr(Proxy, "response_status", "completed")
    monkeypatch.setattr(Proxy, "incomplete_remaining", 0)
    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/v1/responses"

        def run(*args):
            process = subprocess.run([sys.executable, "-B", audit.__file__, *map(str, args)],
                capture_output=True, encoding="utf-8", timeout=20)
            return process.returncode, json.loads(process.stdout)

        try:
            code, first = run("--pack", pack, "--inputs", manifest, "--point", 1,
                              "--responses-url", url, "--model", "fixture-reviewer", "--no-stream")
            assert code == 2, first
            receipt = Path(first["receipt"])
            assert json.loads(receipt.read_bytes())["round"] == 0
            output = reviews / "first.format-correction.json"
            args = ["--receipt", str(receipt), "--expected-receipt-sha256",
                    audit.sha256_hex(receipt.read_bytes()),
                    "--output", str(output), "--responses-url", url, "--model", "fixture-reviewer"]
            candidate = audit.normalized_format_report(data)
            candidate["claim_checks"][0]["finding_ids"] = []
            yield dict(reviews=reviews, protocol=protocol, receipt=receipt, output=output,
                       args=args, candidate=candidate, run=run, url=url, tmp=tmp_path)
        finally:
            server.shutdown()
            thread.join()


def transport(candidate):
    return lambda route, prompt: (200, json.dumps({"status": "completed",
                                                  "output_text": json.dumps(candidate)}))


def correction(w):
    return review_format.main(w["args"], transport=transport(w["candidate"]))


def test_format_recovery_preserves_original_and_allows_only_one_round2(workflow, monkeypatch):
    w = workflow
    before = w["receipt"].read_bytes()
    assert correction(w) == 0
    assert review_format.main(w["args"], transport=lambda *_: pytest.fail("duplicate send")) == 0
    source = json.loads(before)
    restored, info = review_format.recover(w["receipt"], source, 7, w["output"], audit)
    assert restored["round"] == 1 and restored["report"]["decision"] == "未完成"
    assert w["receipt"].read_bytes() == before
    protocol = w["protocol"]
    protocol.write_text("synthetic remediation", encoding="utf-8")
    files = [protocol, w["receipt"], w["output"],
             *[Path(row["path"]) for row in info["artifact_files"]]]
    manifest = w["reviews"] / "second.inputs.json"
    write(manifest, {"review_id": "format-demo", "files": [
        {"path": str(p), "sha256": audit.sha256_hex(p.read_bytes())} for p in dict.fromkeys(files)]})
    pack = w["reviews"] / "second.md"
    pack.write_text("Bound synthetic changes and closure evidence.", encoding="utf-8")
    answer = {"decision": "通过", "summary": "Synthetic closure only.",
              "checklist": [{"item": i, "status": "通过", "reason": "Synthetic"} for i in range(1, 8)],
              "findings": [], "closed_findings": [
                  {"id": "B1", "status": "已解决", "evidence": "Bound synthetic remediation."}]}
    monkeypatch.setattr(Proxy, "response", answer)
    options = ("--inputs", manifest, "--point", 1, "--previous", w["receipt"],
               "--corrected-previous", w["output"], "--responses-url", w["url"],
               "--model", "fixture-reviewer", "--no-stream")
    code, result = w["run"]("--pack", pack, *options)
    assert (code, result.get("gate_passed"), result.get("round")) == (0, True, 2), result
    saved = json.loads(Path(result["receipt"]).read_bytes())
    assert saved["recovered_previous"]["source_sha256"] == audit.sha256_hex(before)
    third = w["reviews"] / "third.md"
    third.write_text("Must not obtain a third substantive round.", encoding="utf-8")
    assert w["run"]("--pack", third, *options)[0] == 3
    assert len(Proxy.requests) == 2
    assert w["receipt"].read_bytes() == before


def test_original_receipt_copy_is_not_another_format_request(workflow):
    w = workflow
    copied = w["reviews"] / "copy.review.json"
    copied.write_bytes(w["receipt"].read_bytes())
    args = list(w["args"])
    args[1] = str(copied)
    assert review_format.main(args, transport=lambda *_: pytest.fail("copy sent")) == 2


def test_removing_local_marker_cannot_reset_shared_request_claim(workflow):
    w = workflow
    assert correction(w) == 0
    Path(str(w["receipt"]) + ".format-request.json").unlink()
    args = list(w["args"])
    args[5] = str(w["reviews"] / "second.format-correction.json")
    assert review_format.main(args, transport=lambda *_: pytest.fail("second format send")) == 2


def test_concurrent_requests_only_one_transport_call(workflow):
    w = workflow
    entered, release = threading.Event(), threading.Event()
    calls = []

    def waiting(route, prompt):
        calls.append(prompt)
        entered.set()
        assert release.wait(10)
        return transport(w["candidate"])(route, prompt)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(review_format.main, w["args"], transport=waiting)
        assert entered.wait(10)
        second_args = list(w["args"])
        second_args[5] = str(w["reviews"] / "other.format-correction.json")
        second = pool.submit(review_format.main, second_args, transport=waiting)
        try:
            assert second.result(timeout=10) == 2
        finally:
            release.set()
        assert first.result(timeout=10) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("body", ["{", "not JSON"])
def test_completed_non_json_is_saved_before_parsing_and_never_retried(workflow, body):
    w = workflow
    raw = json.dumps({"status": "completed", "output_text": body})
    assert review_format.main(w["args"], transport=lambda *_: (200, raw)) == 2
    response = json.loads(Path(str(w["receipt"]) + ".format-request.json.response.json").read_bytes())
    assert response["raw_response"] == raw and response["http_status"] == 200
    assert not w["output"].exists()
    assert review_format.main(w["args"], transport=lambda *_: pytest.fail("retried")) == 2


def test_transport_exception_is_recorded_without_retry(workflow):
    def fail(*_):
        raise TimeoutError("synthetic timeout")
    w = workflow
    assert review_format.main(w["args"], transport=fail) == 2
    response = json.loads(Path(str(w["receipt"]) + ".format-request.json.response.json").read_bytes())
    assert response["failure_kind"] == "transport" and response["http_status"] is None
    assert not w["output"].exists()


@pytest.mark.parametrize("status", [None, "in_progress", "failed", "incomplete"])
def test_absent_or_unsuccessful_terminal_never_creates_correction(workflow, status):
    w = workflow
    raw = json.dumps(dict(status=status, output_text=json.dumps(w["candidate"])))
    assert review_format.main(w["args"], transport=lambda *_: (200, raw)) == 2
    assert not w["output"].exists()
    assert review_format.main(w["args"], transport=lambda *_: pytest.fail("terminal retry")) == 2


@pytest.mark.parametrize("change", ["three_fields", "guard_route", "response", "delete_response", "pack"])
def test_recovery_revalidates_saved_response_request_and_source(workflow, change):
    w = workflow
    assert correction(w) == 0
    if change == "three_fields":
        value = json.loads(w["output"].read_bytes())
        candidate = copy.deepcopy(value["corrected_report"])
        candidate["claim_checks"][0].update(status="存疑", finding_ids=["B1"])
        value.update(corrected_report=candidate, raw_response_report=candidate,
                     raw_response_text=json.dumps(candidate))
        write(w["output"], value)
    elif change == "guard_route":
        path = Path(str(w["receipt"]) + ".format-request.json")
        value = json.loads(path.read_bytes())
        value["route"]["wire_model"] = "another-model"
        write(path, value)
    elif change == "response":
        path = Path(str(w["receipt"]) + ".format-request.json.response.json")
        write(path, {"raw_response": json.dumps({"status": "failed"}), "http_status": 200})
    elif change == "delete_response":
        Path(str(w["receipt"]) + ".format-request.json.response.json").unlink()
    else:
        receipt = json.loads(w["receipt"].read_bytes())
        Path(receipt["pack"]["path"]).write_text("drift", encoding="utf-8")
    with pytest.raises((ValueError, OSError, KeyError)):
        review_format.recover(w["receipt"], json.loads(w["receipt"].read_bytes()), 7, w["output"], audit)


def test_unknown_source_rules_rejected_before_request(workflow):
    w = workflow
    source = json.loads(w["receipt"].read_bytes())
    source["binding"]["rules_sha256"] = "0" * 64
    digest = write(w["receipt"], source)["sha256"]
    args = list(w["args"])
    args[3] = digest
    assert review_format.main(args, transport=lambda *_: pytest.fail("unknown rules sent")) == 2


def test_unresolved_regular_send_blocks_format_request(workflow):
    w = workflow
    receipt = json.loads(w["receipt"].read_bytes())
    ledger = audit.review_ledger
    scope = ledger.validate_scope(receipt["pack"]["path"], receipt["binding"])
    entry = ledger.send_intent(scope, receipt["binding"], receipt["pack"]["path"], 1)
    ledger.append_entries(None, [entry])
    assert review_format.main(w["args"], transport=lambda *_: pytest.fail("pending regular send")) == 2


def test_dynamic_dispatch_import_can_recover(workflow):
    w = workflow
    assert correction(w) == 0
    spec = importlib.util.spec_from_file_location("unregistered_format_dispatch", audit.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    restored, _ = module.recover_corrected_baseline(w["receipt"],
        json.loads(w["receipt"].read_bytes()), 7, w["output"])
    assert restored["round"] == 1


@pytest.mark.parametrize("mode", ["--recovery-template", "--fixture-template"])
def test_corrected_previous_cannot_mix_with_maintenance(workflow, mode):
    code, result = workflow["run"](mode, "synthetic-id", "--corrected-previous", "side.json")
    assert code == 2 and result["stage"] == "args"
