"""Local synthetic demo HTTP and real-browser regression tests."""
import http.client
import json
import shutil
import subprocess
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path

from demo_server import Handler

ROOT = Path(__file__).resolve().parents[1]


class DemoIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_assets_and_paths(self):
        for path, mime in [('/', 'text/html'), ('/app.js', 'text/javascript'), ('/styles.css', 'text/css')]:
            status, headers, body = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertTrue(headers['Content-Type'].startswith(mime))
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertTrue(body)
        for path in ['/../demo_server.py', '/%2e%2e/demo_server.py', '/api/recommend', '/index.html', '/?x=1']:
            self.assertEqual(self.request('GET', path)[0], 404, path)
        self.assertEqual(self.request('GET', '/', headers={'Host': 'evil.example'})[0], 403)

    def test_public_example_served_separately(self):
        status, _, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        html = body.decode('utf-8')
        self.assertIn('id="public-example"', html)
        evidence = json.loads((ROOT / 'sources/public_catalog_kaspersky_edr_20261126.json').read_text())
        self.assertIn(evidence['snapshot']['feed_url'], html)
        for item in [*evidence['offers'].values(), *evidence['manufacturer_sources'].values()]:
            self.assertIn(item['url'], html)
        self.assertIn('KL4867RAKFS', html)
        self.assertIn('KL4867LAKFS', html)

    def test_scenarios(self):
        for scenario in ('b2b', 'b2c', 'cold', 'partial', 'incomplete', 'unresolved'):
            with self.subTest(scenario=scenario):
                status, headers, body = self.request('POST', '/api/recommend', json.dumps({'scenario': scenario}), {'Content-Type': 'application/json'})
                self.assertEqual(status, 200)
                self.assertTrue(headers['Content-Type'].startswith('application/json'))
                data = json.loads(body)
                self.assertIs(data['synthetic_only'], True)
                self.assertTrue(data['description'])
                trace = data['trace']
                self.assertEqual(trace['history_complete'], scenario != 'incomplete')
                self.assertEqual(trace['blocked_reason'], data['blocked_reason'])
                self.assertEqual([x['name'] for x in trace['checks'] if x['accepted']], [x['name'] for x in data['recommendations']])
                if scenario in ('incomplete', 'unresolved'):
                    self.assertEqual(trace['checks'], [])
                if scenario == 'cold':
                    self.assertTrue(all(x['source'] == 'Резервная популярность' for x in data['recommendations']))
                if scenario == 'b2b':
                    self.assertEqual(data['recommendations'][0]['source'], 'Граф совместных заказов')
                    self.assertIn('order-1', data['recommendations'][0]['fixture_evidence'])
                if scenario == 'partial':
                    self.assertTrue(any(not x['accepted'] for x in trace['checks']))
                if scenario in ('incomplete', 'unresolved'):
                    self.assertTrue(data['blocked_reason'])
                    self.assertEqual(data['recommendations'], [])
                else:
                    self.assertIsNone(data['blocked_reason'])
                    self.assertTrue(data['recommendations'])
                    self.assertEqual(bool(data['products']), scenario != 'cold')
                    if scenario == 'partial':
                        self.assertEqual(len(data['recommendations']), 1)
                        self.assertIn('Синтетический продукт В', data['recommendations'][0]['name'])
                    for item in data['recommendations']:
                        self.assertTrue(all(item[k] for k in ('name', 'source', 'explanation', 'fixture_evidence')))

    def test_invalid_requests(self):
        endpoint = '/api/recommend'
        good = '{"scenario":"b2b"}'
        for body in ('{}', '[]', '{{', '{"scenario":"unknown"}', '{"scenario":null}', '{"scenario":"b2b","scenario":"cold"}', '{"scenario":"b2b","other":1}'):
            with self.subTest(body=body):
                self.assertEqual(self.request('POST', endpoint, body, {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request('POST', endpoint, good, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', endpoint, 'x' * 257, {'Content-Type': 'application/json'})[0], 413)
        self.assertEqual(self.request('POST', '/other', good, {'Content-Type': 'application/json'})[0], 404)
        self.assertEqual(self.request('POST', endpoint, good, {'Content-Type': 'application/json', 'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', endpoint, good, {'Content-Type': 'application/json', 'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', endpoint, good, {'Content-Type': 'application/json', 'Origin': f'http://127.0.0.1:{self.port}'})[0], 200)

    def test_browser(self):
        if not shutil.which('node'):
            self.skipTest('Node is unavailable; browser regression not run')
        probe = subprocess.run(['node', '-e', "try {const p=require('playwright'); process.exit(require('fs').existsSync(p.chromium.executablePath())?0:1)} catch(e) {process.exit(1)}"], cwd=ROOT, capture_output=True)
        if probe.returncode:
            self.skipTest('Playwright/Chromium unavailable; browser regression not run')
        run = subprocess.run(['node', str(ROOT / 'tests/demo_browser.cjs'), str(self.port)], cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
