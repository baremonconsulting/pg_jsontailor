#!/usr/bin/env python3
"""PGJT-56 fresh helper ACL, provenance and delete capability matrix."""
import argparse
import itertools
import json
from pathlib import Path

from security_contract_matrix import Database, quote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--psql', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    results = []

    def record(name, condition):
        results.append({'id': 'PGJT-56/' + name, 'status': 'PASS' if condition else 'FAIL'})
        args.result.write_text(''.join(json.dumps(row) + '\n' for row in results))
        assert condition, name

    db.run('DROP SCHEMA IF EXISTS jt56 CASCADE; DROP ROLE IF EXISTS jt56_app; '
           'DROP ROLE IF EXISTS jt56_default; CREATE ROLE jt56_app; CREATE ROLE jt56_default; '
           'CREATE SCHEMA jt56; GRANT USAGE ON SCHEMA jt56 TO jt56_app; '
           'CREATE TABLE jt56.root(id integer PRIMARY KEY,label text); '
           "INSERT INTO jt56.root VALUES(1,'original'); "
           'ALTER DEFAULT PRIVILEGES IN SCHEMA jt56 GRANT EXECUTE ON FUNCTIONS TO jt56_default;')
    spec = {'root': {'table': 'jt56.root', 'key': 'id', 'fields': {'label': 'label'},
                     'insert': True, 'update': True, 'delete': True}}
    try:
        db.run(f"SELECT jsontailor.create_duality_view('jt56.doc',{quote(json.dumps(spec))}::jsonb);")
        record('fresh-owner-only', db.value("""SELECT count(*) FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
 WHERE n.nspname='jt56' AND x.grantee<>p.proowner;""") == '0')
        etag = db.value("SELECT data#>>'{_metadata,etag}' FROM jt56.doc WHERE _id=1;")
        for source, view, rpc in itertools.product((False, True), repeat=3):
            db.run('REVOKE ALL ON jt56.root,jt56.doc FROM jt56_app; '
                   'REVOKE ALL ON FUNCTION jt56.doc__delete_document(integer,text) FROM jt56_app; '
                   'GRANT SELECT ON jt56.root,jt56.doc TO jt56_app;')
            if source:
                db.run('GRANT INSERT,UPDATE,DELETE ON jt56.root TO jt56_app;')
            if view:
                db.run('GRANT DELETE ON jt56.doc TO jt56_app;')
            if rpc:
                db.run('GRANT EXECUTE ON FUNCTION jt56.doc__delete_document(integer,text) TO jt56_app;')
            result = db.run(f"BEGIN; SET ROLE jt56_app; SELECT jt56.doc__delete_document(1,{quote(etag)}); ROLLBACK;", False)
            assert (result.returncode == 0) == (source and view and rpc), (source, view, rpc, result.stderr)
            if result.returncode:
                assert db.state(result) == '42501', result.stderr
            assert db.value("SELECT label FROM jt56.root WHERE id=1;") == 'original'
        record('delete-capability-matrix', True)
        db.run("SELECT jsontailor.grant_delete_rpc('jt56.doc','jt56_app',true); "
               'GRANT EXECUTE ON FUNCTION jt56.doc__delete_document(integer,text) TO PUBLIC; '
               'GRANT EXECUTE ON FUNCTION jt56.doc__check() TO jt56_app; '
               "SELECT jsontailor.regenerate_duality_view('jt56.doc');")
        record('reviewed-provenance', db.value("""SELECT count(*) FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace
 CROSS JOIN LATERAL aclexplode(p.proacl) x
 WHERE n.nspname='jt56' AND x.grantee<>p.proowner
 AND NOT(p.proname='doc__delete_document' AND x.grantee='jt56_app'::regrole
 AND x.grantor=p.proowner AND x.is_grantable);""") == '0'
               and db.value("""SELECT count(*) FROM aclexplode((SELECT proacl FROM pg_proc
 WHERE oid='jt56.doc__delete_document(integer,text)'::regprocedure))
 WHERE grantee='jt56_app'::regrole AND is_grantable;""") == '1')
        db.run('REVOKE EXECUTE ON FUNCTION jt56.doc__delete_document(integer,text) FROM jt56_app; '
               "SELECT jsontailor.regenerate_duality_view('jt56.doc');")
        record('revocation-not-resurrected', db.value("SELECT has_function_privilege('jt56_app',"
               "'jt56.doc__delete_document(integer,text)','EXECUTE');") == 'f')
    finally:
        db.run('ALTER DEFAULT PRIVILEGES IN SCHEMA jt56 REVOKE EXECUTE ON FUNCTIONS FROM jt56_default; '
               'DROP SCHEMA jt56 CASCADE; DROP ROLE jt56_app; DROP ROLE jt56_default;')


if __name__ == '__main__':
    main()
