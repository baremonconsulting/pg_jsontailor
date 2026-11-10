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
from urllib.parse import parse_qsl, unquote, urlsplit

from strict_results import loads as strict_json_loads


SAFE_CODES = frozenset({'22023', '23502', '23503', '23505', '23514', '23P01',
                        '40001', '40P01', '42501', '57014', '55P03',
                        'PT412', 'PT413', 'PT422', 'PT428'})
SAFE_HEADERS = frozenset({'content-range', 'range-unit', 'preference-applied', 'allow'})
MAX_QUERY_LIMIT = 1000


def request_policy(method, query, preferences):
    """Validate the public query and merge mandatory single-document preferences."""
    if re.search(r'%(?![0-9A-Fa-f]{2})', query):
        raise ValueError('invalid query encoding')
    pairs = (parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                       encoding='utf-8', errors='strict', max_num_fields=256)
             if query else [])
    identities = []
    limits = set()
    for key, value in pairs:
        if key.lower() == 'on_conflict':
            raise ValueError('upsert is not supported')
        if key == '_id':
            identities.append(value)
        if key == 'limit' or key.endswith('.limit'):
            if (key in limits or not re.fullmatch(r'[0-9]+', value)
                    or int(value) > MAX_QUERY_LIMIT):
                raise ValueError('invalid or excessive query limit')
            limits.add(key)
    if method == 'PATCH' and (len(identities) != 1
                             or not identities[0].startswith('eq.')
                             or not identities[0][3:]):
        raise ValueError('PATCH requires one exact identity filter')
    merged = {}
    for header in preferences:
        if '\r' in header or '\n' in header:
            raise ValueError('invalid preference header')
        for preference in header.split(','):
            name, separator, value = preference.strip().partition('=')
            name, value = name.strip().lower(), value.strip()
            if not re.fullmatch(r'[a-z][a-z0-9_-]*', name) or name == 'resolution':
                raise ValueError('invalid or unsupported preference')
            item = name + separator + value
            if name in merged and merged[name] != item:
                raise ValueError('conflicting preferences')
            merged[name] = item
    if method == 'PATCH':
        merged['handling'] = 'handling=strict'
        merged['max-affected'] = 'max-affected=1'
    return ', '.join(merged.values())


def within_json_depth(body, limit):
    """Count container nesting before decoding JSON; ignore escaped string bytes."""
    depth = 0
    in_string = escaped = False
    for byte in body:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
        elif byte == 34:
            in_string = True
        elif byte in (91, 123):
            depth += 1
            if depth > limit:
                return False
        elif byte in (93, 125):
            depth -= 1
    return True


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
                 max_json_depth=32, max_response_bytes=16777216,
                 max_connections=16, timeout=10):
        for value, low, high in ((max_request_bytes, 1024, 16777216),
                                 (max_json_depth, 1, 128),
                                 (max_response_bytes, 1024, 67108864),
                                 (max_connections, 1, 256), (timeout, 1, 120)):
            if type(value) is not int or not low <= value <= high:
                raise ValueError('HTTP boundary configuration outside supported bounds')
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
        self.max_json_depth = max_json_depth
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
                if (key.lower() == 'allow'
                        and unquote(self.path.split('?', 1)[0]) in self.server.routes):
                    value = ', '.join(method.strip() for method in value.split(',')
                                      if method.strip().upper() in
                                      {'GET', 'HEAD', 'OPTIONS', 'POST', 'PATCH'})
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
            if self.command == 'DELETE':
                return self.reply(405, safe_error(), [('Allow', 'GET, HEAD, OPTIONS, POST, PATCH')])
            try:
                prefer = request_policy(self.command, url.query, self.headers.get_all('Prefer', []))
            except (ValueError, UnicodeError):
                return self.reply(400, safe_error())
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
            if not within_json_depth(body, self.server.max_json_depth):
                return self.reply(413, safe_error('PT413'))
            if length:
                try:
                    json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except (RuntimeError, ValueError, UnicodeError, RecursionError):
                    return self.reply(400, safe_error())
            headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
            for key in ('Authorization', 'Range', 'Range-Unit'):
                if key in self.headers:
                    headers[key] = self.headers[key]
            if prefer:
                headers['Prefer'] = prefer
            upstream = UnixConnection(self.server.upstream, self.server.timeout_seconds)
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            response_lengths = response.headers.get_all('Content-Length', [])
            response_encodings = response.headers.get_all('Transfer-Encoding', [])
            if (len(response_lengths) > 1 or response_encodings
                    or response_lengths and not re.fullmatch(r'[0-9]+', response_lengths[0])
                    ):
                return self.reply(502, safe_error())
            # Buffer the final response: deferred COMMIT errors are sanitised too.
            raw = response.read(self.server.max_response_bytes + 1)
            if len(raw) > self.server.max_response_bytes:
                return self.reply(502, safe_error())
            # A bounded HTTPResponse.read() accepts premature EOF without raising
            # IncompleteRead. Its remaining length must reach zero before a
            # framed response can be presented as complete to the client.
            if response.length not in (None, 0):
                return self.reply(502, safe_error())
            if self.command != 'HEAD' and response.status not in (204, 304) and raw:
                try:
                    strict_json_loads(raw.decode('utf-8'))
                except (RuntimeError, ValueError, UnicodeError, RecursionError):
                    return self.reply(502, safe_error())
            if response.status >= 300:
                try:
                    data = strict_json_loads(raw.decode('utf-8'))
                    code = data.get('code') if isinstance(data, dict) else None
                except (RuntimeError, ValueError, UnicodeError):
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
    parser.add_argument('--max-request-bytes', type=int, default=1048576)
    parser.add_argument('--max-json-depth', type=int, default=32)
    parser.add_argument('--max-response-bytes', type=int, default=16777216)
    parser.add_argument('--max-connections', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=10)
    args = parser.parse_args()
    with Boundary((args.host, args.port), args.upstream_socket, args.view,
                  max_request_bytes=args.max_request_bytes, max_json_depth=args.max_json_depth,
                  max_response_bytes=args.max_response_bytes,
                  max_connections=args.max_connections, timeout=args.timeout) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
