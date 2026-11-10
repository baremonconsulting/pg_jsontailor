#!/usr/bin/env python3
"""Executable adversarial tests for the supported HTTP sanitising boundary."""
import http.client
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from http_boundary import Boundary, MAX_QUERY_LIMIT, request_policy

SENTINEL = 'UNMAPPED_SOURCE_SECRET_57'


class Upstream(socketserver.UnixStreamServer):
    status = 400
    body = b''
    calls = 0
    declared_length = None
    extra_headers = ()
    omit_length = False


class RawHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.calls += 1
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        self.send_response(self.server.status)
        self.send_header('X-Diagnostics', SENTINEL)
        if not self.server.omit_length:
            self.send_header('Content-Length', str(self.server.declared_length
                             if self.server.declared_length is not None else len(self.server.body)))
        for key, value in self.server.extra_headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(self.server.body)

    do_POST = do_PATCH = do_DELETE = do_OPTIONS = do_GET

    def log_message(self, *_args):
        pass


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pgjt-boundary-')
        self.raw = Upstream(self.directory.name + '/upstream.sock', RawHandler)
        self.raw.requests = []
        Path(self.raw.server_address).chmod(0o600)
        self.raw_thread = threading.Thread(target=self.raw.serve_forever, daemon=True)
        self.raw_thread.start()
        self.boundary = Boundary(('127.0.0.1', 0), self.raw.server_address, ['documents'])
        self.thread = threading.Thread(target=self.boundary.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.boundary.shutdown()
        self.boundary.server_close()
        self.raw.shutdown()
        self.raw.server_close()
        self.directory.cleanup()

    def request(self, path='/documents', headers=None, body=None, method='GET'):
        connection = http.client.HTTPConnection(*self.boundary.server_address, timeout=3)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_all_error_fields_and_headers(self):
        for code, status in [('23514', 400), ('PT412', 412), ('23505', 409),
                             ('arbitrary_' + SENTINEL, 500), ({'secret': SENTINEL}, 500)]:
            self.raw.status = status
            self.raw.body = json.dumps({'code': code, 'message': SENTINEL,
                                       'details': SENTINEL, 'hint': SENTINEL,
                                       'context': SENTINEL}).encode()
            observed, headers, body = self.request()
            self.assertEqual(status, observed)
            self.assertNotIn(SENTINEL, str(headers) + body.decode())
            self.assertEqual(json.loads(body)['code'], code if isinstance(code, str)
                             and code in ('23514', 'PT412', '23505') else 'BOUNDARY_ERROR')

    def test_non_json_and_oversized_errors_fail_closed(self):
        self.raw.body = (SENTINEL + '<html>diagnostics</html>').encode()
        self.assertNotIn(SENTINEL.encode(), self.request()[2])
        self.boundary.max_response_bytes = 2
        self.assertEqual(502, self.request()[0])

    def test_operational_routes_and_openapi_hidden(self):
        for path in ('/', '/rpc/documents__check', '/rpc/duality_view_inventory',
                     '/documents__root_binding', '/orders', '/%2fdocuments'):
            self.assertEqual(404, self.request(path)[0])
        self.assertEqual(404, self.request(headers={'Accept': 'application/openapi+json'})[0])
        self.assertEqual(0, self.raw.calls)

    def test_bad_framing_and_bytes_rejected_before_upstream(self):
        self.assertEqual(400, self.request(headers={'Transfer-Encoding': 'chunked'})[0])
        self.assertEqual(413, self.request(headers={'Content-Length': '1048577'})[0])
        self.assertEqual(0, self.raw.calls)

    def test_pgjt_113_delete_and_non_exact_patch_never_reach_upstream(self):
        cases = [('DELETE', '/documents'), ('DELETE', '/documents?_id=eq.42')]
        cases += [('PATCH', '/documents' + query) for query in (
            '', '?_id=eq.', '?_id=gt.1', '?_id=in.(1,2)', '?_id=eq(any).{1,2}',
            '?_id=eq.1&_id=eq.2', '?_id=eq.1&%5Fid=eq.1',
            '?or=(_id.eq.1,_id.eq.2)', '?_id=eq.42%FF', '?_id=eq.%zz')]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                status, _, body = self.request(path, method=method)
                self.assertEqual(405 if method == 'DELETE' else 400, status)
                self.assertEqual({'code': 'BOUNDARY_ERROR', 'message': 'Request rejected.',
                                  'details': None, 'hint': None, 'context': None}, json.loads(body))
        self.assertEqual(0, self.raw.calls)

    def test_pgjt_113_upsert_preferences_and_limit_fail_closed(self):
        for path in ('/documents?on_conflict=_id', '/documents?on%5Fconflict=_id',
                     '/documents?limit=-1', '/documents?limit=', '/documents?limit=1.0',
                     '/documents?limit=1&limit=2', '/documents?limit=1001',
                     '/documents?child.limit=1001', '/documents?limit=' + '9' * 5000):
            with self.subTest(path=path):
                self.assertEqual(400, self.request(path)[0])
        for prefer in ('resolution=merge-duplicates', 'resolution=ignore-duplicates',
                       'return=representation, Resolution = merge-duplicates',
                       'resolution', 'return=minimal, return=representation'):
            with self.subTest(prefer=prefer):
                status, _, body = self.request(headers={'Prefer': prefer})
                self.assertEqual(400, status)
                self.assertEqual('BOUNDARY_ERROR', json.loads(body)['code'])
        self.assertEqual(0, self.raw.calls)

    def test_pgjt_113_patch_preferences_are_merged_and_forced(self):
        self.raw.status, self.raw.body = 200, b'[]'
        for preferences in ({}, {'Prefer': 'return=representation'},
                            {'Prefer': 'handling=lenient, max-affected=999, count=exact'},
                            {'Prefer': 'handling=strict, max-affected=1'}):
            with self.subTest(preferences=preferences):
                headers = {'Content-Type': 'application/json', **preferences}
                self.assertEqual(200, self.request('/documents?%5Fid=eq.42',
                    method='PATCH', headers=headers, body='{}')[0])
                forwarded = self.raw.requests[-1][2]['Prefer'].split(', ')
                self.assertEqual(1, forwarded.count('handling=strict'))
                self.assertEqual(1, forwarded.count('max-affected=1'))
                self.assertNotIn('handling=lenient', forwarded)
                self.assertNotIn('max-affected=999', forwarded)
                if 'return=representation' in preferences.get('Prefer', ''):
                    self.assertIn('return=representation', forwarded)
        self.assertEqual('return=representation, count=exact, handling=strict, max-affected=1',
                         request_policy('PATCH', '_id=eq.42',
                                        ['return=representation', 'count=exact']))

    def test_pgjt_113_single_identity_values_and_rpc_remain_supported(self):
        self.raw.status, self.raw.body = 200, b'[]'
        for value in ('42', '-1', 'abc', 'a%2Bb', '%22a%2Cb%22',
                      '10000000-0000-0000-0000-000000000001', '2026-11-10'):
            with self.subTest(value=value):
                self.assertEqual(200, self.request('/documents?_id=eq.' + value,
                                                   method='PATCH')[0])
        self.assertEqual(200, self.request('/documents?limit=0')[0])
        self.assertEqual(200, self.request('/documents?limit=' + str(MAX_QUERY_LIMIT))[0])
        self.assertEqual(200, self.request('/rpc/documents__delete_document', method='POST',
            body='{"expected_id":42,"expected_etag":"token"}',
            headers={'Content-Type': 'application/json', 'Prefer': 'return=representation'})[0])
        self.assertEqual('return=representation', self.raw.requests[-1][2]['Prefer'])

    def test_pgjt_113_allow_header_matches_boundary_methods(self):
        self.raw.status, self.raw.body = 200, b'[]'
        self.raw.extra_headers = (('Allow', 'GET, HEAD, OPTIONS, POST, PATCH, DELETE, PUT'),)
        status, headers, _ = self.request(method='OPTIONS')
        self.assertEqual(200, status)
        self.assertEqual('GET, HEAD, OPTIONS, POST, PATCH', headers['Allow'])
        status, headers, _ = self.request(method='DELETE')
        self.assertEqual(405, status)
        self.assertNotIn('DELETE', headers['Allow'])

    def test_depth_and_invalid_json_rejected_before_upstream(self):
        self.assertEqual(413, self.request(method='POST', body='[' * 33 + '0' + ']' * 33,
                                          headers={'Content-Type': 'application/json'})[0])
        self.assertEqual(400, self.request(method='POST', body='{"x":invalid}',
                                          headers={'Content-Type': 'application/json'})[0])
        self.assertEqual(0, self.raw.calls)
        self.raw.status, self.raw.body = 200, b'[]'
        self.assertEqual(200, self.request(method='POST', body=json.dumps({'x': '[[[\\"'}),
                                          headers={'Content-Type': 'application/json'})[0])

    def test_configuration_bounds(self):
        for options in ({'max_request_bytes': 0}, {'max_json_depth': 129},
                        {'max_connections': 0}, {'timeout': 121}):
            with self.assertRaises(ValueError):
                Boundary(('127.0.0.1', 0), self.raw.server_address, ['documents'], **options)

    def test_exact_http_depth_and_byte_boundaries(self):
        self.raw.status, self.raw.body = 200, b'[]'
        self.boundary.max_request_bytes = 1024
        for length, expected in ((1023, 200), (1024, 200), (1025, 413)):
            self.assertEqual(expected, self.request(method='POST', body='{}' + ' ' * (length-2),
                headers={'Content-Type': 'application/json'})[0])
        for depth, expected in ((31, 200), (32, 200), (33, 413)):
            self.assertEqual(expected, self.request(method='POST', body='[' * depth + '0' + ']' * depth,
                headers={'Content-Type': 'application/json'})[0])

    def test_pool_cap_rejects_without_upstream(self):
        acquired = 0
        try:
            while self.boundary.slots.acquire(blocking=False):
                acquired += 1
            self.assertEqual(503, self.request()[0])
            self.assertEqual(0, self.raw.calls)
        finally:
            for _ in range(acquired):
                self.boundary.slots.release()

    def test_upstream_disconnect_fails_closed(self):
        self.raw.shutdown()
        self.raw.server_close()
        Path(self.raw.server_address).unlink()
        self.assertEqual(502, self.request()[0])

    def test_pgjt_95_truncated_success_fails_closed_and_recovers(self):
        self.raw.status, self.raw.body = 200, b'[{"data":'
        self.raw.declared_length = 200
        status, _, body = self.request()
        self.assertEqual(502, status)
        self.assertEqual('BOUNDARY_ERROR', json.loads(body)['code'])
        self.assertNotIn(self.raw.body, body)
        # A complete JSON prefix is still incomplete HTTP framing.
        self.raw.body = b'[]'
        self.assertEqual(502, self.request()[0])
        self.raw.declared_length = None
        self.assertEqual((200, b'[]'), (lambda r: (r[0], r[2]))(self.request()))

    def test_pgjt_95_invalid_json_success_fails_closed(self):
        self.raw.status, self.raw.body = 200, b'[{"data":'
        self.assertEqual(502, self.request()[0])
        self.raw.body = b'[{"data":NaN}]'
        self.assertEqual(502, self.request()[0])

    def test_pgjt_95_ambiguous_upstream_framing_fails_closed(self):
        self.raw.status, self.raw.body = 200, b'[]'
        for headers in ((('Content-Length', '200'),), (('Transfer-Encoding', 'chunked'),)):
            self.raw.extra_headers = headers
            self.assertEqual(502, self.request()[0])
        self.raw.extra_headers = (('Transfer-Encoding', 'chunked'),)
        self.raw.omit_length = True
        self.assertEqual(502, self.request()[0])

    def test_insecure_socket_rejected(self):
        Path(self.raw.server_address).chmod(0o666)
        with self.assertRaises(ValueError):
            Boundary(('127.0.0.1', 0), self.raw.server_address, ['documents'])


if __name__ == '__main__':
    unittest.main()
