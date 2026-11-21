import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dispatch_provider_test", ROOT / "scripts" / "audit_dispatch.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ResponsesProviderTest(unittest.TestCase):
    def args(self, **overrides):
        values = {"provider_config": None, "responses_url": "http://127.0.0.1:8080/v1/responses",
                  "port": None, "api_key_env": "FIXTURE_PROVIDER_TOKEN"}
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_explicit_route_is_secret_free(self):
        route = m.responses_route(self.args(), "fixture-review-model")
        self.assertEqual(route["wire_model"], "fixture-review-model")
        self.assertEqual(route["url"], "http://127.0.0.1:8080/v1/responses")
        self.assertEqual(route["transport"], "responses")
        self.assertNotIn("secret-fixture", repr(route))

    def test_invalid_url_and_embedded_credentials_are_rejected(self):
        for url in ("https://example.com/v1", "https://user:secret@example.com/v1/responses",
                    "https://example.com/v1/responses?key=secret"):
            with self.subTest(url=url), self.assertRaises(m.ProviderNotConfigured):
                m.responses_route(self.args(responses_url=url), "fixture-review-model")
        config = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(pathlib.Path(config.name).unlink)
        with config:
            json.dump({"url": "https://example.com/v1/responses", "model": "fixture", "api_key": "secret"}, config)
        with self.assertRaises(m.ProviderNotConfigured):
            m.responses_route(self.args(provider_config=config.name, responses_url=None), None)

    def test_gateway_sse_failure_is_not_completion(self):
        raw = 'event: response.failed\ndata: {"type":"response.failed","response":{"status":"failed","error":{"code":"fixture_failure"}}}\n\n'
        self.assertTrue(m.has_failed_response(raw))
        self.assertEqual(m.upstream_diagnostic(raw).get("code"), "fixture_failure")

    def test_request_uses_route_and_redacts_secret(self):
        captured = {}

        def request(req, timeout, stream, no_redirect=False):
            captured["request"] = req
            return 200, "secret-fixture"

        route = m.responses_route(self.args(), "fixture-review-model")
        with patch.object(m, "_request_with_deadline", side_effect=request):
            status, raw = m.responses_post(route, "secret-fixture", 5, "fixture", 512, True, "xhigh")
        self.assertEqual(status, 200)
        self.assertEqual(raw, "[redacted]")
        req = captured["request"]
        self.assertEqual(req.full_url, "http://127.0.0.1:8080/v1/responses")
        payload = json.loads(req.data)
        self.assertEqual(payload["model"], "fixture-review-model")
        self.assertEqual(payload["reasoning"], {"effort": "xhigh"})
        self.assertEqual(req.get_header("Authorization"), "Bearer secret-fixture")

    def test_failed_stream_creates_failure_receipt_without_retry_or_append(self):
        raw = 'event: response.failed\ndata: {"type":"response.failed","response":{"status":"failed","error":{"code":"fixture_failure"}}}\n\n'
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            project = root / "project-991-fixture"
            reviews = project / "01-资料" / "reviews"
            reviews.mkdir(parents=True)
            pack = reviews / "pack.md"
            pack.write_text("fixture", encoding="utf-8")
            source = project / "source.md"
            source.write_text("input", encoding="utf-8")
            inputs = project / "inputs.json"
            inputs.write_text(json.dumps({"review_id": "provider-fixture", "files": [
                {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}), encoding="utf-8")
            argv = [str(ROOT / "scripts/audit_dispatch.py"), "--pack", str(pack), "--point", "1",
                    "--inputs", str(inputs), "--responses-url", "http://127.0.0.1:8080/v1/responses",
                    "--model", "fixture-review-model", "--ledger-dir", str(root / "ledger")]
            out = io.StringIO()
            with patch.object(m, "responses_post", return_value=(200, raw)) as send, \
                    patch.object(sys, "argv", argv), contextlib.redirect_stdout(out), \
                    self.assertRaises(SystemExit) as exc:
                m.main()
            self.assertEqual(exc.exception.code, 2)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(pack.read_text(encoding="utf-8"), "fixture")
            result = json.loads(out.getvalue())
            self.assertFalse(result["review_completed"])
            self.assertFalse(result["gate_passed"])
            self.assertEqual(result["failure_kind"], "response_incomplete")
            self.assertEqual(result["upstream"]["code"], "fixture_failure")
            self.assertEqual(len(list(reviews.glob("*.attempt-*.json"))), 1)

    def test_request_does_not_follow_redirect(self):
        self.assertTrue(issubclass(m.NoRedirect, m.urllib.request.HTTPRedirectHandler))
        self.assertIsNone(m.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com"))


if __name__ == "__main__":
    unittest.main()
