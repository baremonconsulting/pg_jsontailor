#!/usr/bin/env python3
"""Fail-closed JSON HTTP boundary for an internal Unix-socket PostgREST service."""
from __future__ import annotations

import argparse
import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit


SAFE_CODES = frozenset({'22023', '23502', '23503', '23505', '23514', '23P01',
                        '40001', '40P01', '42501', '57014', '55P03',
                        'PT412', 'PT413', 'PT422', 'PT428'})
SAFE_HEADERS = frozenset({'content-range', 'range-unit', 'preference-applied', 'allow'})


def safe_error(code='BOUNDARY_ERROR'):
    return json.dumps({'code': code if isinstance(code, str) and code in SAFE_CODES else 'BOUNDARY_ERROR',
                       'message': 'Request rejected.', 'details': None,
                       'hint': None, 'context': None}, separators=(',', ':')).encode()


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=10):
        super().__init__('localhost', timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Boundary(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, upstream, views, *, max_request_bytes=1048576,
                 max_response_bytes=16777216, max_connections=16, timeout=10):
        self.upstream = Path(upstream)
        parent = self.upstream.parent.stat()
        endpoint = self.upstream.stat()
        if (not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_mode & 0o007
                or parent.st_mode & 0o007 or parent.st_uid != os.getuid()
                or endpoint.st_uid != os.getuid()):
            raise ValueError('upstream requires an owner-controlled private Unix socket directory')
        if not views or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,45}', v) for v in views):
            raise ValueError('explicit supported view names are required')
        self.routes = {'/' + v for v in views}
        self.rpcs = {'/rpc/' + v + '__delete_document' for v in views}
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout
        self.slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.settimeout(1)
            try:
                body = safe_error()
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                                + f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'JSONBoundary'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(self.server.timeout_seconds)

    def log_message(self, *_args):
        # Do not log paths, credentials, bodies or upstream diagnostics.
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, safe_error())

    def reply(self, status, body, headers=()):
        self.close_connection = True
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        for key, value in headers:
            if key.lower() in SAFE_HEADERS and '\r' not in value and '\n' not in value:
                self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def handle_request(self):
        upstream = None
        try:
            url = urlsplit(self.path)
            path = unquote(url.path)
            if (url.scheme or url.netloc or url.fragment or self.command not in
                    {'GET', 'HEAD', 'OPTIONS', 'POST', 'PATCH', 'DELETE'}
                    or not (path in self.server.routes or
                            path in self.server.rpcs and self.command == 'POST')
                    or 'openapi' in self.headers.get('Accept', '').lower()):
                return self.reply(404, safe_error())
            lengths = self.headers.get_all('Content-Length', [])
            if (len(lengths) > 1 or self.headers.get('Transfer-Encoding') is not None
                    or self.headers.get('Content-Encoding') is not None
                    or lengths and not re.fullmatch(r'[0-9]+', lengths[0])):
                return self.reply(400, safe_error())
            length = int(lengths[0]) if lengths else 0
            if length > self.server.max_request_bytes:
                return self.reply(413, safe_error('PT413'))
            body = self.rfile.read(length)
            if len(body) != length:
                return self.reply(400, safe_error())
            if length and self.headers.get_content_type() != 'application/json':
                return self.reply(415, safe_error())
            headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
            for key in ('Authorization', 'Prefer', 'Range', 'Range-Unit'):
                if key in self.headers:
                    headers[key] = self.headers[key]
            upstream = UnixConnection(self.server.upstream, self.server.timeout_seconds)
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            # Buffer the final response: deferred COMMIT errors are sanitised too.
            raw = response.read(self.server.max_response_bytes + 1)
            if len(raw) > self.server.max_response_bytes:
                return self.reply(502, safe_error())
            if response.status >= 300:
                try:
                    data = json.loads(raw)
                    code = data.get('code') if isinstance(data, dict) else None
                except (ValueError, UnicodeError):
                    code = None
                return self.reply(response.status, safe_error(code))
            return self.reply(response.status, raw, response.getheaders())
        except (OSError, ValueError, http.client.HTTPException):
            self.reply(502, safe_error())
        finally:
            if upstream:
                upstream.close()

    do_GET = do_HEAD = do_OPTIONS = do_POST = do_PATCH = do_DELETE = handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-socket', type=Path, required=True)
    parser.add_argument('--view', action='append', required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=3050)
    args = parser.parse_args()
    with Boundary((args.host, args.port), args.upstream_socket, args.view) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
