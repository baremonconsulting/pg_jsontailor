#!/usr/bin/env python3
"""PGJT-56/57 live PostgREST tests, including deferred COMMIT rejection."""
import argparse
import json
from pathlib import Path
import socket
import sys
import time

from run_postgrest import request
from security_contract_matrix import Database
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from http_boundary import MAX_QUERY_LIMIT, UnixConnection, safe_error


def write_boundary_contract(base, db, record):
    """PGJT-113: rejected public writes leave every source row unchanged."""
    snapshot_sql = """SELECT jsonb_build_object(
 'root',(SELECT jsonb_agg(to_jsonb(o) ORDER BY order_id) FROM shop.orders o),
 'children',(SELECT jsonb_agg(to_jsonb(i) ORDER BY item_id) FROM shop.order_items i))::text;"""
    _, _, raw = request(base, 'GET', '/orders_dv?_id=eq.42')
    original = json.loads(raw)[0]['data']
    new_document = {'status': 'boundary-check', 'customer': {'customerId': 7},
                    'items': [{'product': 'boundary-check', 'qty': 1, 'price': 1}]}
    status, _, raw = request(base, 'POST', '/orders_dv', {'data': new_document},
                             {'Prefer': 'return=representation'})
    assert status == 201, 'PGJT-113 fixture insert failed'
    inserted = json.loads(raw)[0]
    ident = inserted['_id']
    try:
        snapshot = db.value(snapshot_sql)

        def rejected(method, path, headers=None):
            status, _, raw = request(base, method, path, {'data': original}, headers)
            return (status == (405 if method == 'DELETE' else 400)
                    and json.loads(raw) == json.loads(safe_error())
                    and db.value(snapshot_sql) == snapshot)

        write_cases = [('DELETE', '/orders_dv'), ('DELETE', '/orders_dv?_id=eq.42')]
        write_cases += [('PATCH', '/orders_dv' + query) for query in (
            '', '?_id=eq.', '?_id=gt.1', '?_id=eq.42&_id=eq.42',
            '?_id=eq.42&%5Fid=eq.42')]
        write_results = [rejected(method, path) for method, path in write_cases]
        record('PGJT-113/http-write-boundary', all(write_results))

        preference_results = [rejected('POST', '/orders_dv?on_conflict=_id')]
        for value in ('merge-duplicates', 'ignore-duplicates'):
            preference_results.append(rejected('POST', '/orders_dv',
                                               {'Prefer': 'resolution=' + value}))
        preference_results.append(rejected('GET', '/orders_dv?limit=' + str(MAX_QUERY_LIMIT + 1)))
        record('PGJT-113/http-query-preferences', all(preference_results))

        document = inserted['data']
        document['status'] = 'boundary-updated'
        patch_status, headers, raw = request(base, 'PATCH', f'/orders_dv?_id=eq.{ident}',
            {'data': document}, {'Prefer': 'return=representation, handling=lenient, max-affected=999'})
        patched = json.loads(raw)[0] if patch_status == 200 else None
        patch_ok = (patch_status == 200 and patched['_id'] == ident
                    and patched['data']['status'] == 'boundary-updated'
                    and 'return=representation' in headers.get('Preference-Applied', ''))
        _, _, raw = request(base, 'GET', '/orders_dv?_id=eq.42')
        unchanged_seed = json.loads(raw)[0]['data'] == original
        delete_ok = False
        if patched is not None:
            delete_status, _, raw = request(base, 'POST', '/rpc/orders_dv__delete_document', {
                'expected_id': ident, 'expected_etag': patched['data']['_metadata']['etag']})
            delete_ok = (delete_status == 200 and json.loads(raw) == patched['data']
                         and db.value(f'SELECT count(*) FROM shop.orders WHERE order_id={ident};') == '0')
        record('PGJT-113/http-patch-rpc-compatibility', patch_ok and unchanged_seed and delete_ok)
    finally:
        # Remove only the generated test document if a contract assertion failed.
        db.run(f'DELETE FROM shop.order_items WHERE order_id={ident}; '
               f'DELETE FROM shop.orders WHERE order_id={ident};')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--psql', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--base', default='http://127.0.0.1:3050')
    parser.add_argument('--raw-port', type=int, default=3052)
    parser.add_argument('--upstream-socket', type=Path, required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    results = []

    def record(test_id, ok):
        results.append({'id': test_id, 'status': 'PASS' if ok else 'FAIL'})
        args.result.write_text(''.join(json.dumps(row) + '\n' for row in results))
        assert ok, test_id

    db.run("""ALTER TABLE shop.orders ADD COLUMN secret text NOT NULL DEFAULT 'UNMAPPED_SECRET_57';
 ALTER TABLE shop.orders ADD CONSTRAINT internal_secret_check CHECK(status<>'reject-immediate');
 CREATE FUNCTION shop.reject_deferred() RETURNS trigger LANGUAGE plpgsql AS $f$
 BEGIN IF NEW.status='reject-deferred' THEN
 RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='UNMAPPED_SECRET_57',
 DETAIL=NEW.secret,HINT='UNMAPPED_SECRET_57'; END IF; RETURN NEW; END $f$;
 CREATE CONSTRAINT TRIGGER reject_deferred AFTER UPDATE ON shop.orders
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION shop.reject_deferred();""")
    try:
        write_boundary_contract(args.base, db, record)
        for phase in ('immediate', 'deferred'):
            status, _, raw = request(args.base, 'GET', '/orders_dv?_id=eq.42')
            original = json.loads(raw)[0]['data']
            document = json.loads(json.dumps(original))
            document['status'] = 'reject-' + phase
            document['items'][0]['qty'] += 1
            snapshot_sql = """SELECT jsonb_build_object(
 'root',(SELECT jsonb_agg(to_jsonb(o) ORDER BY order_id) FROM shop.orders o),
 'children',(SELECT jsonb_agg(to_jsonb(i) ORDER BY item_id) FROM shop.order_items i))::text;"""
            snapshot = db.value(snapshot_sql)
            raw_connection = UnixConnection(args.upstream_socket)
            raw_connection.request('PATCH', '/orders_dv?_id=eq.42',
                                   json.dumps({'data': document}),
                                   {'Content-Type': 'application/json', 'Prefer': 'return=representation'})
            control = raw_connection.getresponse()
            control_status, control_body = control.status, control.read().decode()
            raw_connection.close()
            record('PGJT-57/' + phase + '-raw-disclosure-control',
                   control_status == 400 and 'UNMAPPED_SECRET_57' in control_body
                   and db.value(snapshot_sql) == snapshot)
            status, headers, raw = request(args.base, 'PATCH', '/orders_dv?_id=eq.42',
                                           {'data': document}, {'Prefer': 'return=representation'})
            _, _, after = request(args.base, 'GET', '/orders_dv?_id=eq.42')
            record('PGJT-57/' + phase + '-sentinel-rollback', status == 400
                   and json.loads(raw)['code'] == '23514'
                   and all(value not in raw + str(headers) for value in
                           ('UNMAPPED_SECRET_57', 'internal_secret_check', 'shop', 'orders'))
                   and json.loads(after)[0]['data'] == original
                   and db.value(snapshot_sql) == snapshot)
        for path in ('/', '/rpc/orders_dv__check', '/rpc/duality_view_inventory'):
            record('PGJT-57/hidden-' + path.replace('/', '_'), request(args.base, 'GET', path)[0] == 404)
        with socket.socket() as probe:
            probe.settimeout(1)
            record('PGJT-57/raw-upstream-not-tcp', probe.connect_ex(('127.0.0.1', args.raw_port)) != 0)
        status, _, _ = request(args.base, 'POST', '/rpc/fresh_acl_dv__delete_document',
                                {'expected_id': 42, 'expected_etag': original['_metadata']['etag']})
        record('PGJT-56/http-no-rpc-capability', status in (401, 403, 404)
               and db.value("SELECT has_function_privilege('jt_postgrest_anon',"
                   "'api.fresh_acl_dv__delete_document(bigint,text)','EXECUTE');") == 'f'
               and db.value('SELECT count(*) FROM shop.orders WHERE order_id=42;') == '1')

        # PGJT-142: force fresh pool connections to inherit a deterministic
        # statement timeout, then prove that the public boundary preserves only
        # the allow-listed SQLSTATE and no server diagnostic.
        db.run("ALTER ROLE jt_postgrest_authenticator SET statement_timeout='1ms'; "
               "ALTER ROLE jt_postgrest_anon SET statement_timeout='1ms'; "
               "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
               "WHERE usename='jt_postgrest_authenticator' AND pid<>pg_backend_pid();")
        timeout_status = None; timeout_raw = ''
        try:
            for _ in range(30):
                timeout_status, _, timeout_raw = request(
                    args.base, 'GET', '/orders_dv?_id=gte.10000&order=_id')
                if timeout_status not in (502, 503):
                    break
                time.sleep(0.2)
            try:
                timeout_body = json.loads(timeout_raw)
            except json.JSONDecodeError:
                timeout_body = {}
        finally:
            db.run("ALTER ROLE jt_postgrest_authenticator SET statement_timeout='30s'; "
                   "ALTER ROLE jt_postgrest_anon SET statement_timeout='30s'; "
                   "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                   "WHERE usename='jt_postgrest_authenticator' AND pid<>pg_backend_pid();")
        record('PGJT-142/postgrest-statement-timeout', timeout_status == 500
               and timeout_body.get('code') == '57014'
               and timeout_body.get('message') == 'Request rejected.'
               and all(token not in timeout_raw.lower() for token in
                       ('statement timeout', 'shop.', 'orders')))
    finally:
        db.run('DROP TRIGGER reject_deferred ON shop.orders; DROP FUNCTION shop.reject_deferred(); '
               'ALTER TABLE shop.orders DROP CONSTRAINT internal_secret_check; '
               'ALTER TABLE shop.orders DROP COLUMN secret; '
               "SELECT jsontailor.grant_delete_rpc('api.orders_dv','jt_postgrest_anon',false);")


if __name__ == '__main__':
    main()
