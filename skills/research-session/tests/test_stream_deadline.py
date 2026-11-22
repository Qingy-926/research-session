import contextlib
import hashlib
import http.server
import io
import importlib.util
import json
import pathlib
import ssl
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stream_deadline_dispatch', ROOT / 'scripts' / 'audit_dispatch.py')
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)


class StreamHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    mode = 'terminal'
    requests = 0
    def log_message(self, *args):
        pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        type(self).requests += 1
        if self.mode == 'slow_header':
            time.sleep(1.5)
        if self.mode == 'delayed_body':
            time.sleep(0.55)
        if self.mode == 'trickle_headers':
            try:
                self.wfile.write(b'HTTP/1.1 200 OK\r\n')
                for _ in range(10):
                    self.wfile.write(b'X-Fixture: heartbeat\r\n')
                    self.wfile.flush()
                    time.sleep(0.2)
            except (ConnectionError, OSError):
                pass
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        try:
            if self.mode == 'delayed_body':
                time.sleep(1.2)
                self.wfile.write(b'data: {"type":"response.completed"}\n\n')
                self.wfile.flush()
            elif self.mode == 'terminal':
                self.wfile.write(b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n')
                self.wfile.flush()
                time.sleep(1.5)
            elif self.mode == 'failed':
                self.wfile.write(b'data: {"type":"response.failed","response":{"status":"failed"}}\n\n')
                self.wfile.flush()
                time.sleep(1.5)
            else:
                for _ in range(12):
                    self.wfile.write(b': heartbeat\n\n')
                    self.wfile.flush()
                    time.sleep(0.15)
        except (ConnectionError, OSError):
            pass
        self.close_connection = True


class StreamDeadlineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), StreamHandler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
    def send(self, timeout):
        return dispatch.http_post_responses(self.server.server_port, timeout, 'fixture', 'fixture', 100, True)
    def test_completed_event_does_not_wait_for_connection_close(self):
        StreamHandler.mode = 'terminal'
        start = time.monotonic()
        status, body = self.send(3)
        elapsed = time.monotonic() - start
        self.assertEqual(status, 200)
        self.assertFalse(dispatch.has_failed_response(body))
        self.assertLess(elapsed, 1.0, 'terminal event was held until connection close')
    def test_heartbeats_do_not_extend_total_deadline(self):
        StreamHandler.mode = 'heartbeat'
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.send(0.5)
        self.assertLess(time.monotonic() - start, 1.2)


    def test_responses_transport_does_not_follow_redirect_to_another_service(self):
        class Target(http.server.BaseHTTPRequestHandler):
            seen = False
            def log_message(self, *args): pass
            def do_GET(self):
                Target.seen = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')
        target = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Target)
        target.daemon_threads = True
        threading.Thread(target=target.serve_forever, daemon=True).start()
        class Redirect(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(302)
                self.send_header('Location', 'http://127.0.0.1:%d/other' % target.server_port)
                self.end_headers()
        redirect = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Redirect)
        redirect.daemon_threads = True
        threading.Thread(target=redirect.serve_forever, daemon=True).start()
        try:
            status, _ = dispatch.http_post_responses(redirect.server_port, 2, 'fixture', 'fixture', 100, True)
            self.assertEqual(status, 302)
            self.assertFalse(Target.seen)
        finally:
            redirect.shutdown(); redirect.server_close()
            target.shutdown(); target.server_close()

    def test_failed_terminal_event_is_not_accepted(self):
        StreamHandler.mode = 'failed'
        _, body = self.send(3)
        self.assertTrue(dispatch.has_failed_response(body))

    def test_deadline_covers_wait_for_response_headers(self):
        StreamHandler.mode = 'slow_header'
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.send(0.5)
        self.assertLess(time.monotonic() - start, 1.2)

    def test_provider_transport_uses_the_same_total_deadline(self):
        StreamHandler.mode = 'heartbeat'
        route = {'url': 'http://127.0.0.1:%d/responses' % self.server.server_port,
                 'wire_model': 'fixture', 'transport': 'responses', 'provider': 'fixture', 'api_key_env': None}
        with self.assertRaises(TimeoutError):
            dispatch.responses_post(route, 'test-token', 0.5, 'fixture', 100, True, 'xhigh')

    def test_https_handler_bounds_header_and_body_waits(self):
        route = {'url': 'https://127.0.0.1:%d/responses' % self.server.server_port,
                 'wire_model': 'fixture', 'transport': 'responses',
                 'provider': 'fixture', 'api_key_env': None}
        # Keep HTTPS handler selection real; substitute TLS only for this loopback server.
        with patch.object(ssl.SSLContext, 'wrap_socket',
                          new=lambda context, sock, **kwargs: sock):
            for mode in ('trickle_headers', 'delayed_body'):
                with self.subTest(mode=mode):
                    StreamHandler.mode = mode
                    start = time.monotonic()
                    with self.assertRaises(TimeoutError):
                        dispatch.responses_post(route, None, 1, 'fixture', 100, True, 'xhigh')
                    self.assertLess(time.monotonic() - start, 1.4)

    def test_timeout_fails_closed_keeps_send_guard_and_never_resends(self):
        StreamHandler.mode = 'heartbeat'
        StreamHandler.requests = 0
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            project = root / 'project-991-fixture'
            reviews = project / '01-资料' / 'reviews'
            reviews.mkdir(parents=True)
            pack = reviews / 'pack.md'
            pack.write_text('fixture', encoding='utf-8')
            source = project / 'source.md'
            source.write_text('fixture input', encoding='utf-8')
            inputs = project / 'inputs.json'
            inputs.write_text(json.dumps({'review_id': 'deadline-fixture', 'files': [{'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}), encoding='utf-8')
            argv = ['audit_dispatch.py', '--pack', str(pack), '--point', '1', '--inputs', str(inputs),
                    '--port', str(self.server.server_port), '--model', 'fixture-review-model',
                    '--timeout', '1', '--ledger-dir', str(root / 'ledger')]
            stdout = io.StringIO()
            with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(stdout), self.assertRaises(SystemExit) as result:
                dispatch.main()
            data = json.loads(stdout.getvalue())
            self.assertEqual(result.exception.code, 2)
            self.assertEqual(data['failure_kind'], 'transport')
            self.assertFalse(data['review_completed'])
            self.assertFalse(data['gate_passed'])
            self.assertEqual(StreamHandler.requests, 1)
            self.assertEqual(pack.read_text(encoding='utf-8'), 'fixture')
            self.assertEqual(len(list(reviews.glob('.review-send-*.lock'))), 1)
            self.assertFalse((reviews / 'pack.md.review.json').exists())
            self.assertEqual(len(list(reviews.glob('*.attempt-*.json'))), 1)

class ResponseContractTest(unittest.TestCase):
    def test_prior_transport_version_can_continue_only_with_explicit_binding(self):
        files = ['scripts/audit_dispatch.py', 'references/codex-review-pack.md', 'SKILL.md', 'references/autonomy-bounds.md', 'references/review-evidence.md', 'scripts/review_evidence.py', 'scripts/review_ledger.py', 'scripts/review_format.py', 'scripts/review_task.py', 'references/project-work.md', 'references/preregistration.md', 'references/independent-review.md', 'references/autonomous-start.md', 'references/retrieval-evidence.md', 'references/claim-evidence.md']
        digest = hashlib.sha256()
        for name in files:
            digest.update((ROOT / name).read_bytes())
        manifest = (ROOT / 'references/review-rules-compatibility.json').read_bytes()
        binding = {'rules_sha256': digest.hexdigest(), 'compatibility_manifest_sha256': hashlib.sha256(manifest).hexdigest()}
        old = 'bc3bc3a47b5de01aa2e459b85076cffbf15cdb3de8e6023cc9d14aa80852e901'
        self.assertEqual(dispatch.compatible_rules_transition(old, binding)['previous_rules_sha256'], old)

    def test_completed_response_with_final_text_and_no_deltas_is_read(self):
        event = {'type': 'response.completed', 'response': {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'final review fixture'}]}]}}
        raw = 'event: response.completed\ndata: ' + json.dumps(event) + '\n\n'
        self.assertFalse(dispatch.has_failed_response(raw))
        self.assertEqual(dispatch.extract_text(raw), 'final review fixture')

    def test_event_header_supplies_missing_type_for_complete_response(self):
        payload = {'response': {'status': 'completed', 'output_text': 'review fixture'}}
        raw = 'event: response.completed\ndata: ' + json.dumps(payload) + '\n\n'
        self.assertFalse(dispatch.has_failed_response(raw))
        self.assertEqual(dispatch.extract_text(raw), 'review fixture')

    def test_empty_completed_payload_cannot_validate_pass_delta(self):
        delta = {'type': 'response.output_text.delta', 'delta': '{"decision":"通过"}'}
        raw = 'data: ' + json.dumps(delta, ensure_ascii=False) + '\n\nevent: response.completed\ndata: {}\n\n'
        self.assertTrue(dispatch.has_failed_response(raw))

    def test_conflicting_terminal_event_types_fail_closed(self):
        raw = 'event: response.failed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n'
        self.assertTrue(dispatch.has_failed_response(raw))

    def test_final_response_supersedes_incomplete_delta(self):
        delta = {'type': 'response.output_text.delta', 'delta': 'part'}
        event = {'type': 'response.completed', 'response': {'status': 'completed', 'output_text': 'complete'}}
        raw = 'data: ' + json.dumps(delta) + '\n\ndata: ' + json.dumps(event) + '\n\n'
        self.assertEqual(dispatch.extract_text(raw), 'complete')

    def test_patch_format_is_explicit_in_prompt_contract(self):
        repair = dispatch.review_contract(1)['repair']
        self.assertIn('nonempty string', repair.get('patch', ''))

if __name__ == '__main__':
    unittest.main()
