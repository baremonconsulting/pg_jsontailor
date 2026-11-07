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
from http_boundary import Boundary

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
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.send_response(self.server.status)
        self.send_header('X-Diagnostics', SENTINEL)
        if not self.server.omit_length:
            self.send_header('Content-Length', str(self.server.declared_length
                             if self.server.declared_length is not None else len(self.server.body)))
        for key, value in self.server.extra_headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(self.server.body)

    do_POST = do_GET

    def log_message(self, *_args):
        pass


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pgjt-boundary-')
        self.raw = Upstream(self.directory.name + '/upstream.sock', RawHandler)
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
