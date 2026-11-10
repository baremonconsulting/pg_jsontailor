#!/usr/bin/env python3
"""Run both synthetic fixtures through a private PostgREST boundary rehearsal."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from checks import operational_read_sample

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / 'pilot/synthetic'
sys.path.insert(0, str(ROOT / 'tools'))
from http_boundary import Boundary, MAX_QUERY_LIMIT

# Fixed identifiers: failures must never shrink the reviewed result inventory.
RESULT_IDS = (
    'PGJT-112/synthetic-health', 'PGJT-112/synthetic-readback',
    'PGJT-112/synthetic-filter-paging', 'PGJT-112/synthetic-insert',
    'PGJT-112/synthetic-replacement', 'PGJT-112/synthetic-etags',
    'PGJT-112/synthetic-errors', 'PGJT-112/synthetic-rest-surface',
    'PGJT-112/synthetic-delete-rpc', 'PGJT-112/synthetic-operational-read',
    'PGJT-112/synthetic-mixed-types', 'PGJT-113/synthetic-write-boundary',
    'PGJT-112/synthetic-restore', 'PGJT-112/synthetic-cleanup',
)


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def normalise(doc):
    if not isinstance(doc, dict):
        return doc
    value = copy.deepcopy(doc)
    value.pop('_metadata', None)
    value.pop('links', None)
    if 'created' in value:
        value['created'] = str(value['created'])[:10]
    if 'observed' in value:
        value['observed'] = datetime.fromisoformat(value['observed'].replace('Z', '+00:00')).isoformat()
    for item in value.get('items', []):
        item['price'] = float(item['price'])
    return value


def fixture_cardinality(documents):
    if not documents or len({doc['_id'] for doc in documents}) != len(documents):
        raise ValueError('fixture requires distinct root identities')
    return len(documents), sum(len(doc.get('items', [])) for doc in documents)


def validate_databases(database, restore_database):
    for name in (database, restore_database):
        if (not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,62}', name)
                or name in {'postgres', 'template0', 'template1'}):
            raise ValueError('pilot databases must be dedicated disposable database names')
    if database == restore_database:
        raise ValueError('pilot and restore databases must be distinct')


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, default=Path(os.environ.get(
        'PG_JSONTAILOR_PG_BIN', str(Path(shutil.which('pg_config') or '/usr/bin/pg_config').parent))))
    parser.add_argument('--pg-host', default=os.environ.get('PGHOST', str(ROOT / '.run/socket')))
    parser.add_argument('--pg-port', default=os.environ.get('PGPORT', '55433'))
    parser.add_argument('--pg-user', default=os.environ.get('PGUSER', os.environ.get('USER', 'postgres')))
    parser.add_argument('--postgrest', type=Path, default=Path(os.environ.get(
        'PG_JSONTAILOR_POSTGREST', shutil.which('postgrest') or str(ROOT / '.tools/postgrest-v16.5/postgrest'))))
    parser.add_argument('--postgrest-db-host', default='127.0.0.1')
    parser.add_argument('--rest-host', default='127.0.0.1')
    parser.add_argument('--rest-port', type=int, default=3060)
    parser.add_argument('--admin-port', type=int, default=3061)
    parser.add_argument('--database', default='jsontailor_synthetic_pilot')
    parser.add_argument('--restore-database', default='jsontailor_synthetic_pilot_restore')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--result', type=Path, help='Fresh JSONL output path; defaults inside --output')
    args = parser.parse_args(argv)
    try:
        validate_databases(args.database, args.restore_database)
    except ValueError as error:
        parser.error(str(error))
    return args


class Rehearsal:
    def __init__(self, args):
        self.args = args
        self.run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        self.out = args.output or ROOT / 'results' / ('pilot-' + self.run_id)
        self.result_path = args.result or self.out / 'synthetic-pilot-results.jsonl'
        if self.result_path.exists():
            raise ValueError('never overwrite existing pilot results')
        self.out.mkdir(parents=True, exist_ok=False)
        self.raw = self.out / 'raw'
        self.raw.mkdir(mode=0o700)
        self.env = {**os.environ, 'PGHOST': args.pg_host, 'PGPORT': str(args.pg_port),
                    'PGUSER': args.pg_user}
        self.documents = [json.loads(path.read_text()) for path in sorted((PILOT / 'documents').glob('*.json'))]
        self.root_count, self.child_count = fixture_cardinality(self.documents)
        self.mixed_documents = json.loads((PILOT / 'mixed_types/documents.json').read_text())
        fixture_cardinality(self.mixed_documents)
        token = uuid.uuid4().hex[:12]
        self.app_role, self.auth_role = 'jt_pilot_app_' + token, 'jt_pilot_auth_' + token
        self.databases, self.roles = [], []
        self.events, self.checks = [], {}
        self.server = self.boundary = self.boundary_thread = None
        self.log = None
        self.socket_directory = None
        self.origin = f'http://{args.rest_host}:{args.rest_port}'

    def run(self, command, *, input_text=None, check=True):
        result = subprocess.run([str(part) for part in command], input=input_text, text=True,
                                capture_output=True, env=self.env, timeout=120)
        if check and result.returncode:
            raise RuntimeError(f'command failed: {command[0]}\n{result.stdout}\n{result.stderr}')
        return result

    def sql(self, text, database=None, check=True):
        return self.run([self.args.pg_bin / 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1',
                         '-d', database or self.args.database], input_text=text, check=check)

    def scalar(self, text, database=None):
        return self.sql(text, database).stdout.strip().splitlines()[-1]

    def add(self, name, passed, observed, expected):
        if name not in RESULT_IDS or name in self.checks:
            raise ValueError('unknown or duplicate pilot result identifier')
        self.checks[name] = {'name': name, 'status': 'PASS' if passed else 'FAIL',
                             'observed': observed, 'expected': expected}

    def http(self, label, method='GET', query='', document=None, prefer=False,
             raw_body=False, route='/orders_dv'):
        body = None if document is None else document if raw_body else {'data': document}
        payload = None if body is None else json.dumps(body, separators=(',', ':')).encode()
        headers = {'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        if prefer:
            headers['Prefer'] = 'return=representation'
        url = self.origin + route + query
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status, response_headers, raw = response.status, dict(response.headers), response.read().decode()
        except urllib.error.HTTPError as error:
            status, response_headers, raw = error.code, dict(error.headers), error.read().decode()
        elapsed = (time.perf_counter() - started) * 1000
        parsed = json.loads(raw) if raw else None
        self.events.append({'label': label, 'method': method, 'url': url, 'request': body,
                            'status': status, 'response_headers': response_headers,
                            'response': parsed, 'elapsed_ms': round(elapsed, 3)})
        return status, parsed, elapsed

    def read(self, ident, route='/orders_dv'):
        status, rows, _ = self.http('read for operation', query=f'?_id=eq.{ident}', route=route)
        if status != 200 or not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError('expected one synthetic document')
        return rows[0]['data']

    def create_database(self, name):
        self.run([self.args.pg_bin / 'createdb', name])
        self.databases.append(name)  # Only successful creations may be removed.

    def setup(self):
        names = ','.join(literal(name) for name in (self.args.database, self.args.restore_database))
        if self.scalar(f'SELECT count(*) FROM pg_database WHERE datname IN ({names});', 'postgres') != '0':
            raise RuntimeError('refusing existing pilot or restore database; nothing was removed')
        self.create_database(self.args.database)
        self.sql("CREATE EXTENSION pg_jsontailor; CREATE SCHEMA pilot; CREATE SCHEMA pilot_api;\n"
                 "CREATE TABLE pilot.customers(customer_id bigint PRIMARY KEY,name text NOT NULL);\n"
                 "CREATE TABLE pilot.orders(order_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,customer_id bigint REFERENCES pilot.customers,status text NOT NULL DEFAULT 'new',created_at date NOT NULL DEFAULT CURRENT_DATE);\n"
                 "CREATE TABLE pilot.order_items(item_id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,order_id bigint NOT NULL REFERENCES pilot.orders,product text NOT NULL,quantity integer NOT NULL CHECK(quantity>0),unit_price numeric(12,2) NOT NULL);\n"
                 "CREATE INDEX ON pilot.order_items(order_id,item_id);\n"
                 "INSERT INTO pilot.customers VALUES(7,'ACME'),(8,'Globex'),(9,'Northwind');")
        for doc in self.documents:
            self.sql(f"INSERT INTO pilot.orders(order_id,customer_id,status,created_at) VALUES({doc['_id']},{doc['customer']['customerId']},{literal(doc['status'])},{literal(doc['created'])}::date);\n" +
                     '\n'.join(f"INSERT INTO pilot.order_items(item_id,order_id,product,quantity,unit_price) VALUES({item['itemId']},{doc['_id']},{literal(item['product'])},{item['qty']},{item['price']});" for item in doc['items']))
        root_next = max(doc['_id'] for doc in self.documents) + 1000
        child_next = max((item['itemId'] for doc in self.documents for item in doc['items']), default=0) + 10000
        self.sql(f"SELECT setval(pg_get_serial_sequence('pilot.orders','order_id'),{root_next});"
                 f"SELECT setval(pg_get_serial_sequence('pilot.order_items','item_id'),{child_next});")
        self.sql((PILOT / 'mixed_types/schema.sql').read_text())
        for view, path in (('orders_dv', PILOT / 'spec.json'),
                           ('mixed_dv', PILOT / 'mixed_types/spec.json')):
            self.sql(f"SELECT jsontailor.create_duality_view('pilot_api.{view}',{literal(path.read_text())}::jsonb);")
        self.sql(f'CREATE ROLE {self.app_role} NOLOGIN;')
        self.roles.append(self.app_role)
        self.sql(f"CREATE ROLE {self.auth_role} LOGIN PASSWORD 'pilot-local-only';")
        self.roles.append(self.auth_role)
        # Isolated rehearsal grants, not a customer deployment template.
        self.sql(f"GRANT {self.app_role} TO {self.auth_role};\n"
                 f"ALTER ROLE {self.app_role} SET statement_timeout='5s';\n"
                 f"GRANT USAGE ON SCHEMA pilot,pilot_api,pilot_mixed TO {self.app_role};\n"
                 f"GRANT SELECT,INSERT,UPDATE,DELETE ON pilot_api.orders_dv,pilot_api.mixed_dv,pilot.orders,pilot.order_items,pilot_mixed.readings TO {self.app_role};\n"
                 f"GRANT SELECT ON pilot.customers TO {self.app_role};\n"
                 f"SELECT jsontailor.grant_array_reader('pilot_api.orders_dv','{self.app_role}',false);\n"
                 f"SELECT jsontailor.grant_delete_rpc('pilot_api.orders_dv','{self.app_role}',false);\n"
                 f"SELECT jsontailor.grant_delete_rpc('pilot_api.mixed_dv','{self.app_role}',false);")
        health = [self.scalar(f"SELECT pilot_api.{view}__check()->>'status';")
                  for view in ('orders_dv', 'mixed_dv')]
        counts = self.scalar("SELECT count(*)::text||':'||(SELECT count(*) FROM pilot.order_items) FROM pilot.orders;")
        expected = f'{self.root_count}:{self.child_count}'
        self.add('PGJT-112/synthetic-health', health == ['OK', 'OK'] and counts == expected,
                 {'health': health, 'cardinality': counts}, {'health': ['OK', 'OK'], 'cardinality': expected})
        self.dump = self.raw / 'pre_pilot.dump'
        self.run([self.args.pg_bin / 'pg_dump', '-Fc', '-d', self.args.database, '-f', self.dump])

    def start_http(self):
        self.socket_directory = tempfile.TemporaryDirectory(prefix='pgjt-pilot-http-')
        socket_path = Path(self.socket_directory.name) / 'upstream.sock'
        config = self.raw / 'postgrest.conf'
        database = urllib.parse.quote(self.args.database, safe='')
        uri = (f'postgresql://{self.auth_role}:pilot-local-only@'
               f'{self.args.postgrest_db_host}:{self.args.pg_port}/{database}')
        config.write_text(f'db-uri = {json.dumps(uri)}\n'
                          'db-schemas = "pilot_api"\n'
                          f'db-anon-role = "{self.app_role}"\n'
                          f'db-max-rows = {MAX_QUERY_LIMIT}\n'
                          'server-host = "127.0.0.1"\nserver-port = 0\n'
                          f'server-unix-socket = {json.dumps(str(socket_path))}\n'
                          'server-unix-socket-mode = "600"\n'
                          f'admin-server-port = {self.args.admin_port}\nlog-level = "warn"\n')
        config.chmod(0o600)
        self.log = (self.raw / 'postgrest.log').open('w')
        self.server = subprocess.Popen([str(self.args.postgrest), str(config)],
                                       stdout=self.log, stderr=subprocess.STDOUT, env=self.env)
        ready = f'http://127.0.0.1:{self.args.admin_port}/ready'
        for _ in range(80):
            if self.server.poll() is not None:
                raise RuntimeError('PostgREST exited before readiness')
            try:
                with urllib.request.urlopen(ready, timeout=.2):
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(.1)
        else:
            raise RuntimeError('PostgREST did not become ready')
        self.boundary = Boundary((self.args.rest_host, self.args.rest_port), socket_path,
                                 ['orders_dv', 'mixed_dv'])
        self.boundary_thread = threading.Thread(target=self.boundary.serve_forever, daemon=True)
        self.boundary_thread.start()

    def orders(self):
        readbacks = [normalise(self.read(doc['_id'])) == normalise(doc) for doc in self.documents]
        self.add('PGJT-112/synthetic-readback', all(readbacks), readbacks, 'every seeded document matches')
        status, rows, _ = self.http('filter and paging', query='?data-%3E%3Estatus=eq.fulfilled&limit=1&offset=0')
        self.add('PGJT-112/synthetic-filter-paging', status == 200 and len(rows) == 1 and rows[0]['_id'] == 203,
                 {'status': status, 'rows': rows}, '200 and document 203')
        inserted = {'status': 'new', 'customer': {'customerId': 8},
                    'items': [{'product': 'Generated', 'qty': 1, 'price': 3.25}]}
        status, body, _ = self.http('insert generated keys', 'POST', document=inserted, prefer=True)
        self.add('PGJT-112/synthetic-insert', status == 201 and isinstance(body, list)
                 and len(body) == 1 and isinstance(body[0].get('_id'), int), status, '201 and generated identity')
        current = self.read(202)
        current['status'] = 'approved'
        current['items'][0]['qty'] = 4
        current['items'].append({'product': 'New item', 'qty': 1, 'price': 8.75})
        status, _, _ = self.http('replace current document', 'PATCH', '?_id=eq.202', current, True)
        relational = self.scalar("SELECT status||':'||(SELECT sum(quantity)::text FROM pilot.order_items WHERE order_id=202) FROM pilot.orders WHERE order_id=202;")
        self.add('PGJT-112/synthetic-replacement', status == 200 and relational == 'approved:5',
                 {'status': status, 'state': relational}, '200 and approved:5')
        first = self.read(203)
        stale = copy.deepcopy(first)
        first['status'], stale['status'] = 'client-a', 'client-b'
        first_status, _, _ = self.http('client A write', 'PATCH', '?_id=eq.203', first, True)
        stale_status, _, _ = self.http('client B stale write', 'PATCH', '?_id=eq.203', stale, True)
        self.add('PGJT-112/synthetic-etags', (first_status, stale_status) == (200, 412),
                 [first_status, stale_status], [200, 412])
        valid = self.read(204)
        no_etag, missing, unknown = (copy.deepcopy(valid) for _ in range(3))
        no_etag.pop('_metadata')
        missing.pop('status')
        unknown['unexpected'] = True
        errors = [self.http(label, 'PATCH', '?_id=eq.204', value, True)[0]
                  for label, value in (('missing ETag', no_etag), ('missing field', missing), ('unknown field', unknown))]
        self.add('PGJT-112/synthetic-errors', errors == [428, 422, 422] and self.read(204) == valid,
                 errors, [428, 422, 422])
        surface = [self.http('hidden resource', route=path)[0]
                   for path in ('/orders', '/orders_dv__root_binding')]
        self.add('PGJT-112/synthetic-rest-surface', surface == [404, 404], surface, [404, 404])
        deleted = self.read(205)
        status, body, _ = self.http('delete with ETag', 'POST', document={
            'expected_id': 205, 'expected_etag': deleted['_metadata']['etag']},
            raw_body=True, route='/rpc/orders_dv__delete_document')
        remaining = self.scalar('SELECT count(*) FROM pilot.orders WHERE order_id=205;')
        children = self.scalar('SELECT count(*) FROM pilot.order_items WHERE order_id=205;')
        self.add('PGJT-112/synthetic-delete-rpc', status == 200 and body == deleted
                 and remaining == children == '0', {'status': status, 'rows': remaining, 'children': children}, '200 and no source rows')
        passed, observed = operational_read_sample(self.http)
        self.add('PGJT-112/synthetic-operational-read', passed, observed,
                 '20 valid local reads under 1000 ms; diagnostic only')

    def mixed_types(self):
        results = []
        for documents in (self.mixed_documents, list(reversed(self.mixed_documents))):
            values = ','.join('(' + literal(json.dumps(doc)) + '::jsonb)' for doc in documents)
            self.sql(f'BEGIN; SET LOCAL ROLE {self.app_role}; '
                     f'INSERT INTO pilot_api.mixed_dv(data) VALUES {values}; '
                     'UPDATE pilot_api.mixed_dv SET data=data; ROLLBACK;')
            results.append(self.scalar('SELECT count(*) FROM pilot_mixed.readings;') == '0')
        for document in self.mixed_documents:
            status, rows, _ = self.http('mixed-type insert', 'POST', document=document,
                                        prefer=True, route='/mixed_dv')
            results.append(status == 201 and len(rows) == 1
                           and normalise(rows[0]['data']) == normalise(document))
            current = self.read(document['_id'], '/mixed_dv')
            current['n'] += 1
            current['ok'] = not current['ok']
            current['description'] += ' updated'
            status, _, _ = self.http('mixed-type replacement', 'PATCH', f"?_id=eq.{document['_id']}",
                                      current, True, route='/mixed_dv')
            results.append(status == 200 and normalise(self.read(document['_id'], '/mixed_dv')) == normalise(current))
            results.append(self.scalar(f"SELECT qty={current['n']} AND active IS {str(current['ok']).upper()} "
                                      f"AND label={literal(current['description'])} FROM pilot_mixed.readings "
                                      f"WHERE reading_id={document['_id']};") == 't')
        self.add('PGJT-112/synthetic-mixed-types', all(results), results,
                 'mixed numeric/boolean/timestamptz/text writes in both transaction orders and through the proxy')

    def boundary_contract(self):
        snapshot_sql = "SELECT jsonb_build_object('roots',(SELECT jsonb_agg(to_jsonb(o) ORDER BY order_id) FROM pilot.orders o),'children',(SELECT jsonb_agg(to_jsonb(i) ORDER BY item_id) FROM pilot.order_items i))::text;"
        before = self.scalar(snapshot_sql)
        document = self.read(201)
        results = []
        for method, query, expected in (('DELETE', '', 405), ('PATCH', '', 400),
                                         ('PATCH', '?_id=eq.201&_id=eq.202', 400)):
            status, body, _ = self.http('rejected broad write', method, query, document)
            results.append(status == expected and isinstance(body, dict)
                           and body.get('code') == 'BOUNDARY_ERROR'
                           and self.scalar(snapshot_sql) == before)
        self.add('PGJT-113/synthetic-write-boundary', all(results), results, 'safe 4xx and no source mutation')

    def restore(self):
        self.create_database(self.args.restore_database)
        result = self.run([self.args.pg_bin / 'pg_restore', '--no-owner', '--exit-on-error',
                           '-d', self.args.restore_database, self.dump], check=False)
        (self.raw / 'restore.log').write_text(result.stdout + result.stderr)
        database = self.args.restore_database
        counts = self.scalar("SELECT count(*)::text||':'||(SELECT count(*) FROM pilot.order_items)||':'||(SELECT count(*) FROM pilot_api.orders_dv) FROM pilot.orders;", database)
        expected = f'{self.root_count}:{self.child_count}:{self.root_count}'
        restored = json.loads(self.scalar(f'SET ROLE {self.app_role}; SELECT jsonb_agg(data ORDER BY _id) FROM pilot_api.orders_dv;', database))
        health = [self.scalar(f"SELECT pilot_api.{view}__check()->>'status';", database)
                  for view in ('orders_dv', 'mixed_dv')]
        self.add('PGJT-112/synthetic-restore', result.returncode == 0 and counts == expected
                 and [normalise(doc) for doc in restored] == [normalise(doc) for doc in sorted(self.documents, key=lambda doc: doc['_id'])]
                 and health == ['OK', 'OK'] and self.scalar('SELECT count(*) FROM pilot_mixed.readings;', database) == '0',
                 {'restore_rc': result.returncode, 'state': counts, 'health': health}, expected)

    def cleanup(self):
        errors = []

        def attempt(action):
            try:
                action()
            except Exception as error:
                errors.append(f'{type(error).__name__}: {error}')

        if self.boundary is not None:
            if self.boundary_thread is not None and self.boundary_thread.is_alive():
                attempt(self.boundary.shutdown)
                attempt(lambda: self.boundary_thread.join(timeout=5))
            attempt(self.boundary.server_close)
        if self.server is not None:
            def stop_server():
                self.server.terminate()
                try:
                    self.server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.server.kill()
                    self.server.wait(timeout=5)
            attempt(stop_server)
        if self.log is not None:
            attempt(self.log.close)
        if self.socket_directory is not None:
            attempt(self.socket_directory.cleanup)
        for database in reversed(self.databases):
            attempt(lambda database=database: self.run([self.args.pg_bin / 'dropdb', '--if-exists', database]))
        for role in reversed(self.roles):
            attempt(lambda role=role: self.sql(f'DROP ROLE {role};', 'postgres'))
        return errors

    def execute(self):
        failure = None
        try:
            self.setup()
            self.start_http()
            self.orders()
            self.mixed_types()
            self.boundary_contract()
            self.restore()
        except Exception as error:
            failure = f'{type(error).__name__}: {error}'
            (self.raw / 'failure.log').write_text(failure + '\n')
        finally:
            try:
                cleanup_errors = self.cleanup()
            except Exception as error:
                cleanup_errors = [f'{type(error).__name__}: {error}']
            self.add('PGJT-112/synthetic-cleanup', not cleanup_errors,
                     cleanup_errors, 'only owned processes, newly created databases and unique roles removed')
            for name in RESULT_IDS:
                if name not in self.checks:
                    self.add(name, False, 'not completed; see private failure.log', 'completed scenario')
            rows = [self.checks[name] for name in RESULT_IDS]
            (self.raw / 'http_transcript.json').write_text(json.dumps(self.events, ensure_ascii=False, indent=2))
            (self.out / 'test_results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            self.result_path.parent.mkdir(parents=True, exist_ok=True)
            self.result_path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))
            failed = sum(row['status'] != 'PASS' for row in rows)
            summary = {'run_id': self.run_id, 'scope': 'synthetic pre-pilot',
                       'documents': self.root_count, 'children': self.child_count,
                       'mixed_documents': len(self.mixed_documents), 'checks': len(rows),
                       'passed': len(rows) - failed, 'failed': failed,
                       'decision': 'GO' if not failed and failure is None else 'NO-GO',
                       'limitation': 'Synthetic technical evidence only; no authorisation for customer data, a customer pilot, production or release.'}
            (self.out / 'summary.json').write_text(json.dumps(summary, indent=2))
            print(json.dumps({**summary, 'output': str(self.out), 'result': str(self.result_path)}, indent=2))
        return 0 if summary['decision'] == 'GO' else 1


def main(argv=None):
    return Rehearsal(arguments(argv)).execute()


if __name__ == '__main__':
    raise SystemExit(main())
