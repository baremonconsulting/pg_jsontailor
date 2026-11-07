#!/usr/bin/env python3
"""PGJT-70 concurrent API hot-root, object-lock, deadlock and retry campaign."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time

from run_postgrest import request
from runtime_limits_matrix import Session
from security_contract_matrix import Database, quote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--psql', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--base', default='http://127.0.0.1:3050')
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    rows = []

    def record(name, ok):
        rows.append({'id': 'PGJT-70/http-' + name, 'status': 'PASS' if ok else 'FAIL'})
        args.result.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        assert ok, name

    def document(ident):
        status, _, raw = request(args.base, 'GET', f'/concurrent_dv?_id=eq.{ident}')
        assert status == 200, (status, raw)
        return json.loads(raw)[0]['data']

    def patch(ident, doc):
        return request(args.base, 'PATCH', f'/concurrent_dv?_id=eq.{ident}', {'data': doc},
                       {'Prefer': 'return=representation'})

    original = document(42)
    object_name = db.value('SELECT name FROM shop.customers WHERE customer_id=7;')
    writer = None
    db.run("INSERT INTO shop.orders(order_id,customer_id,status,created_at) VALUES(43,8,'other-root','2026-11-03');")
    try:
        barrier = threading.Barrier(3)
        def hot(label):
            doc = json.loads(json.dumps(original))
            doc['status'] = label
            barrier.wait(timeout=5)
            return patch(42, doc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(hot, label) for label in ('hot-a', 'hot-b')]
            barrier.wait(timeout=5)
            responses = [future.result(timeout=10) for future in futures]
        record('hot-root-stale-token', sorted(row[0] for row in responses) == [200, 412]
               and all(json.loads(raw).get('code') == 'PT412'
                       for status, _, raw in responses if status == 412)
               and document(42)['status'] in ('hot-a', 'hot-b'))

        before = document(42)
        writer = Session(db)
        writer.send("BEGIN; SET statement_timeout='5s'; UPDATE shop.customers SET name='object-raced' WHERE customer_id=7;")
        writer.ready()
        changed = json.loads(json.dumps(before))
        changed['status'] = 'must-not-commit'
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(patch, 42, changed)
            deadline = time.monotonic() + 5
            reached = False
            while time.monotonic() < deadline:
                reached = db.value("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE "
                    "usename='jt_postgrest_authenticator' AND wait_event_type='Lock');") == 't'
                if reached:
                    break
                time.sleep(.02)
            assert reached, 'API request did not reach the object-lock barrier'
            writer.finish()
            status, _, raw = future.result(timeout=10)
        after = document(42)
        record('object-lock-stale-rollback', status == 412 and json.loads(raw)['code'] == 'PT412'
               and after['status'] == before['status']
               and after['_metadata']['etag'] != before['_metadata']['etag']
               and after['items'] == before['items'])

        # Test-only source trigger: two explicit advisory-lock start barriers,
        # followed by opposite object updates while each document owns SHARE.
        db.run("""CREATE FUNCTION shop.jt70_deadlock() RETURNS trigger LANGUAGE plpgsql AS $f$
 DECLARE n integer:=0; deadline timestamptz:=clock_timestamp()+interval '5 seconds';
 BEGIN
 IF NEW.status LIKE 'deadlock-70-%' THEN
   PERFORM pg_advisory_xact_lock(7070,NEW.order_id::integer);
   LOOP
     SELECT count(DISTINCT objid) INTO n FROM pg_locks WHERE locktype='advisory'
       AND classid=7070 AND objid IN(42,43) AND granted;
     EXIT WHEN n=2;
     IF clock_timestamp()>deadline THEN RAISE EXCEPTION 'deadlock start barrier timed out';END IF;
     PERFORM pg_sleep(0.01);
   END LOOP;
   UPDATE shop.customers SET name=name WHERE customer_id=CASE NEW.order_id WHEN 42 THEN 8 ELSE 7 END;
 END IF;
 RETURN NEW;END $f$;
 CREATE TRIGGER jt70_deadlock AFTER UPDATE ON shop.orders FOR EACH ROW EXECUTE FUNCTION shop.jt70_deadlock();""")
        pre = {ident: document(ident) for ident in (42, 43)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = {}
            for ident in (42, 43):
                doc = json.loads(json.dumps(pre[ident]))
                doc['status'] = f'deadlock-70-{ident}'
                tasks[ident] = pool.submit(patch, ident, doc)
            responses = {ident: future.result(timeout=12) for ident, future in tasks.items()}
        db.run('DROP TRIGGER jt70_deadlock ON shop.orders; DROP FUNCTION shop.jt70_deadlock();')
        failures = [ident for ident, response in responses.items() if response[0] == 500
                    and json.loads(response[2])['code'] == '40P01']
        successes = [ident for ident, response in responses.items() if response[0] == 200]
        assert len(failures) == len(successes) == 1, responses
        failed = failures[0]
        record('deadlock-full-rollback', document(failed) == pre[failed]
               and document(successes[0])['status'] == f'deadlock-70-{successes[0]}')
        retry = document(failed)
        retry['status'] = f'deadlock-70-{failed}'
        status, _, raw = patch(failed, retry)
        record('deadlock-complete-transaction-retry', status == 200
               and json.loads(raw)[0]['data'] == document(failed)
               and document(failed)['status'] == retry['status'])
    finally:
        if writer:
            writer.close()
        db.run('DROP TRIGGER IF EXISTS jt70_deadlock ON shop.orders; DROP FUNCTION IF EXISTS shop.jt70_deadlock(); '
               'DELETE FROM shop.orders WHERE order_id=43;')
        db.run('UPDATE shop.customers SET name=' + quote(object_name) + ' WHERE customer_id=7;')


if __name__ == '__main__':
    main()
