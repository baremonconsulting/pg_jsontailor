#!/usr/bin/env python3
"""PGJT-56/57 live PostgREST tests, including deferred COMMIT rejection."""
import argparse
import json
from pathlib import Path
import socket
import sys

from run_postgrest import request
from security_contract_matrix import Database
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from http_boundary import UnixConnection


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
        db.run('REVOKE EXECUTE ON FUNCTION api.orders_dv__delete_document(bigint,text) FROM jt_postgrest_anon; '
               'NOTIFY pgrst,\'reload schema\';')
        status, _, _ = request(args.base, 'POST', '/rpc/orders_dv__delete_document',
                                {'expected_id': 42, 'expected_etag': original['_metadata']['etag']})
        record('PGJT-56/http-no-rpc-capability', status in (401, 403, 404))
    finally:
        db.run('DROP TRIGGER reject_deferred ON shop.orders; DROP FUNCTION shop.reject_deferred(); '
               'ALTER TABLE shop.orders DROP CONSTRAINT internal_secret_check; '
               'ALTER TABLE shop.orders DROP COLUMN secret; '
               "SELECT jsontailor.grant_delete_rpc('api.orders_dv','jt_postgrest_anon',false);")


if __name__ == '__main__':
    main()
