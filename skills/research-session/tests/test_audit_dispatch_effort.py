import contextlib, hashlib, http.server, json, pathlib, subprocess, sys, tempfile, threading, unittest
from importlib.util import spec_from_file_location, module_from_spec

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'audit_dispatch.py'

class Proxy(http.server.BaseHTTPRequestHandler):
    requests = []
    reject = False
    stream_error = False
    point = 1
    def log_message(self, *args): pass
    def do_POST(self):
        self.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(400 if self.reject else 200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if self.stream_error:
            content = (
                'event: response.output_text.delta\n'
                'data: {"type":"response.output_text.delta","delta":"总体裁决：放行"}\n\n'
                'event: response.failed\n'
                'data: {"type":"response.failed","response":{"error":{"message":"unsupported effort"}}}\n\n'
            )
            self.wfile.write(content.encode('utf-8'))
        else:
            review = {
                'decision': '通过', 'summary': 'Fixture review.', 'findings': [], 'closed_findings': [],
                'checklist': [{'item': i, 'status': '通过', 'reason': ''}
                              for i in range(1, {1: 7, 2: 5, 3: 5}[self.point] + 1)],
            }
            content = {'error': 'unsupported reasoning'} if self.reject else {'status': 'completed', 'output_text': json.dumps(review, ensure_ascii=False)}
            self.wfile.write(json.dumps(content, ensure_ascii=False).encode('utf-8'))

class DispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(('127.0.0.1', 0), Proxy)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close()
    def setUp(self):
        Proxy.requests.clear(); Proxy.reject = False; Proxy.stream_error = False
    def invoke(self, point, stream=False):
        with tempfile.TemporaryDirectory() as td:
            project = pathlib.Path(td) / 'project-991-fixture'
            reviews = project / '01-资料' / 'reviews'
            reviews.mkdir(parents=True)
            pack = reviews / 'pack.md'
            pack.write_text('复核包测试内容', encoding='utf-8')
            source = project / 'protocol.md'
            source.write_text('frozen fixture source', encoding='utf-8')
            manifest = project / 'inputs.json'
            manifest.write_text(json.dumps({'review_id': 'effort-fixture', 'files': [
                {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}), encoding='utf-8')
            Proxy.point = point
            cmd = [sys.executable, '-B', str(SCRIPT), '--pack', str(pack), '--point', str(point),
                   '--inputs', str(manifest), '--port', str(self.server.server_port),
                   '--model', 'fixture-review-model',
                   '--ledger-dir', str(pathlib.Path(td) / 'ledger')]
            if not stream: cmd.append('--no-stream')
            run = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
            return run, pack.read_text(encoding='utf-8')
    def test_each_point_requests_xhigh_and_records_requested_effort(self):
        for point in (1,2,3):
            with self.subTest(point=point):
                Proxy.requests.clear()
                run, text = self.invoke(point)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertEqual(len(Proxy.requests), 1)
                self.assertEqual(Proxy.requests[0].get('model'), 'fixture-review-model')
                self.assertEqual(Proxy.requests[0].get('reasoning'), {'effort':'xhigh'})
                self.assertIn('xhigh', text)
    def test_rejected_effort_fails_without_downgrade_or_append(self):
        Proxy.reject = True
        run, text = self.invoke(1)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(len(Proxy.requests), 1)
        self.assertEqual(Proxy.requests[0].get('reasoning'), {'effort':'xhigh'})
        self.assertEqual(text, '复核包测试内容')
    def test_stream_failure_after_verdict_is_not_accepted(self):
        Proxy.stream_error = True
        run, text = self.invoke(1, stream=True)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(text, '复核包测试内容')

    def test_shared_http_call_does_not_change_other_callers(self):
        spec = spec_from_file_location('dispatch', SCRIPT); module = module_from_spec(spec); spec.loader.exec_module(module)
        status, _ = module.http_post_responses(self.server.server_port, 3, 'other-model', 'test', 100, False)
        self.assertEqual(status, 200)
        self.assertNotIn('reasoning', Proxy.requests[0])

if __name__ == '__main__': unittest.main(verbosity=2)
