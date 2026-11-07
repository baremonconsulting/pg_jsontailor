#!/usr/bin/env python3
"""PGJT-86/87/89/91/92/94 database contract regressions."""
import argparse
import json
from pathlib import Path

from security_contract_matrix import Database, quote
from runtime_limits_matrix import Session


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--psql', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    rows = []

    def record(name, ok):
        rows.append({'id': name, 'status': 'PASS' if ok else 'FAIL'})
        args.result.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        assert ok, name

    def create(name, spec):
        db.run('SELECT jsontailor.create_duality_view(' + quote('rc02.' + name)
               + ',' + quote(json.dumps(spec)) + '::jsonb);')

    db.run('DROP SCHEMA IF EXISTS rc02 CASCADE;CREATE SCHEMA rc02;')
    try:
        for width in (39, 40, 49, 50, 51, 81):
            fields = {f'f{i}': f'f{i}' for i in range(width)}
            columns = ','.join(f'f{i} text DEFAULT ' + quote(f'value{i}') for i in range(width))
            db.run(f'CREATE TABLE rc02.root{width}(id integer PRIMARY KEY,{columns});'
                   f'CREATE TABLE rc02.obj{width}(id integer PRIMARY KEY,{columns});'
                   f'ALTER TABLE rc02.root{width} ADD COLUMN obj integer REFERENCES rc02.obj{width}(id);'
                   f'CREATE TABLE rc02.child{width}(id integer PRIMARY KEY,parent integer REFERENCES rc02.root{width}(id),{columns});'
                   f'CREATE INDEX ON rc02.child{width}(parent,id);'
                   f'INSERT INTO rc02.obj{width}(id) VALUES(1);'
                   f'INSERT INTO rc02.root{width}(id,obj) VALUES(1,1);'
                   f'INSERT INTO rc02.child{width}(id,parent) VALUES(1,1);')
            spec = {'root': {'table': f'rc02.root{width}', 'key': 'id', 'fields': fields,
                             'insert': True, 'update': True, 'delete': True},
                    'object': {'table': f'rc02.obj{width}', 'key': 'id', 'root_fk': 'obj',
                               'name': 'detail', 'fields': {'id': 'id', **fields}},
                    'array': {'table': f'rc02.child{width}', 'key': 'id', 'parent_fk': 'parent',
                              'name': 'items', 'fields': {'id': 'id', **fields},
                              'insert': True, 'update': True, 'delete': True}}
            create(f'wide{width}', spec)
            doc = json.loads(db.value(f'SELECT data FROM rc02.wide{width} WHERE _id=1;'))
            ok = all(doc[k] == f'value{i}' and doc['detail'][k] == f'value{i}'
                     and doc['items'][0][k] == f'value{i}' for i, k in enumerate(fields))
            db.run(f"UPDATE rc02.wide{width} SET data=jsonb_set(data,'{{f0}}','\"changed\"') WHERE _id=1;")
            record(f'PGJT-86/wide-{width}', ok and db.value(
                f"SELECT data->>'f0' FROM rc02.wide{width} WHERE _id=1;") == 'changed')

        db.run('CREATE TABLE rc02.small(id integer PRIMARY KEY,label text DEFAULT ' + quote('q' * 2048) + ');')
        spec = {'root': {'table': 'rc02.small', 'key': 'id', 'fields': {'label': 'label'},
                         'insert': True, 'update': True, 'delete': True}, 'limits': {'request_bytes': 1024}}
        create('small_docs', spec)
        for name, doc in [('metadata-expansion', {'_id': 1, 'label': 'q' * 970}),
                          ('default-expansion', {'_id': 2})]:
            result = db.run('INSERT INTO rc02.small_docs(data) VALUES(' + quote(json.dumps(doc)) + '::jsonb);', False)
            record('PGJT-92/' + name, db.state(result) == 'PT413'
                   and db.value('SELECT count(*) FROM rc02.small;') == '0')
        db.run("INSERT INTO rc02.small_docs(data) VALUES('{\"_id\":3,\"label\":\"small\"}');"
               'UPDATE rc02.small_docs SET data=data WHERE _id=3;')
        record('PGJT-92/accepted-roundtrip', db.value('SELECT count(*) FROM rc02.small;') == '1')

        for isolation in ('REPEATABLE READ', 'SERIALIZABLE'):
            for name, statement in [
                ('insert', "INSERT INTO rc02.small_docs(data) VALUES('{\"_id\":4,\"label\":\"small\"}')"),
                ('update', 'UPDATE rc02.small_docs SET data=data WHERE _id=3'),
                ('delete', 'DELETE FROM rc02.small_docs WHERE _id=3'),
                ('no-match', 'UPDATE rc02.small_docs SET data=data WHERE false'),
                ('merge', 'MERGE INTO rc02.small_docs d USING (VALUES(3))s(id) ON d._id=s.id WHEN MATCHED THEN UPDATE SET data=d.data'),
                ('rpc', "SELECT rc02.small_docs__delete_document(3,'stale')")]:
                result = db.run(f'BEGIN ISOLATION LEVEL {isolation};{statement};COMMIT;', False)
                record(f'PGJT-87/{isolation.lower().replace(" ", "-")}-{name}', db.state(result) == '0A000'
                       and db.value('SELECT count(*) FROM rc02.small;') == '1')

        db.run('DROP INDEX rc02.child39_parent_id_idx;')
        record('PGJT-94/inventory-missing-index', db.value(
            "SELECT health_status FROM jsontailor.duality_view_inventory() WHERE view_name='rc02.wide39';") == 'PT422')
        db.run('CREATE INDEX ON rc02.child39(parent,id);')
        record('PGJT-94/inventory-healthy', db.value(
            "SELECT health_status FROM jsontailor.duality_view_inventory() WHERE view_name='rc02.wide39';") == 'OK')
        result = db.run("UPDATE rc02.wide39 SET data=jsonb_set(data,'{detail,id}','9999') WHERE _id=1;", False)
        record('PGJT-89/missing-object-owner', db.state(result) == '23503')
        db.run('CREATE ROLE rc02_sql_app;GRANT USAGE ON SCHEMA rc02 TO rc02_sql_app;'
               'GRANT SELECT,UPDATE ON rc02.wide39,rc02.root39,rc02.obj39,rc02.child39 TO rc02_sql_app;'
               'GRANT EXECUTE ON FUNCTION rc02.wide39__bounded_array(bigint) TO rc02_sql_app;'
               'INSERT INTO rc02.obj39(id) VALUES(9999);'
               'ALTER TABLE rc02.obj39 ENABLE ROW LEVEL SECURITY;'
               'CREATE POLICY rc02_select ON rc02.obj39 FOR SELECT TO rc02_sql_app USING(id=1);'
               'CREATE POLICY rc02_update ON rc02.obj39 FOR UPDATE TO rc02_sql_app USING(id=1);')
        outcomes = []
        for key in (9999, 10000):
            result = db.run('SET ROLE rc02_sql_app;UPDATE rc02.wide39 SET data='
                            + "jsonb_set(data,'{detail,id}'," + quote(str(key)) + ') WHERE _id=1;', False)
            outcomes.append(db.state(result))
        record('PGJT-89/no-hidden-existence-leak', outcomes == ['42501', '42501'])

        for isolation in ('REPEATABLE READ', 'SERIALIZABLE'):
            session = Session(db)
            try:
                session.send('\\set VERBOSITY verbose')
                session.send(f'BEGIN ISOLATION LEVEL {isolation};DO $$BEGIN PERFORM data FROM rc02.wide39 WHERE _id=1;END$$;')
                session.ready()
                db.run('INSERT INTO rc02.child39(id,parent) VALUES(2,1);')
                session.send('UPDATE rc02.wide39 SET data=data WHERE _id=1;COMMIT;')
                session.process.stdin.close()
                session.process.wait(timeout=10)
                stderr = session.process.stderr.read()
                record(f'PGJT-87/direct-child-after-snapshot-{isolation.lower().replace(" ", "-")}',
                       session.process.returncode != 0 and '0A000' in stderr
                       and db.value("SELECT data->>'f0' FROM rc02.wide39 WHERE _id=1;") == 'changed')
            finally:
                session.close()
                db.run('DELETE FROM rc02.child39 WHERE id=2;')

        db.run('CREATE TABLE rc02.toast_root(id integer PRIMARY KEY,payload text);'
               'CREATE TABLE rc02.toast_child(id integer PRIMARY KEY,parent integer REFERENCES rc02.toast_root(id),payload text,qty integer);'
               'ALTER TABLE rc02.toast_root ALTER payload SET STORAGE EXTERNAL;'
               'ALTER TABLE rc02.toast_child ALTER payload SET STORAGE EXTERNAL;'
               "INSERT INTO rc02.toast_root SELECT 1,string_agg(md5(i::text),'') FROM generate_series(1,1024)i;"
               'INSERT INTO rc02.toast_child SELECT 1,1,payload,1 FROM rc02.toast_root;'
               'CREATE INDEX ON rc02.toast_child(parent,id);')
        create('toast_docs', {'root': {'table': 'rc02.toast_root', 'key': 'id',
               'fields': {'payload': 'payload'}, 'update': True},
               'array': {'table': 'rc02.toast_child', 'key': 'id', 'parent_fk': 'parent',
                         'name': 'items', 'fields': {'id': 'id', 'payload': 'payload', 'qty': 'qty'},
                         'update': True}})

        def chunks(table):
            toast = db.value('SELECT reltoastrelid::regclass FROM pg_class WHERE oid='
                             + quote('rc02.' + table) + '::regclass;')
            return db.value('SELECT array_agg(chunk_id::text||' + quote(':')
                            + '||chunk_seq::text ORDER BY chunk_id,chunk_seq)::text FROM ' + toast + ';')

        before_root, before_child = chunks('toast_root'), chunks('toast_child')
        db.run("UPDATE rc02.toast_docs SET data=jsonb_set(data,'{items,0,qty}','2') WHERE _id=1;")
        record('PGJT-91/unchanged-toast-preserved', before_root == chunks('toast_root')
               and before_child == chunks('toast_child')
               and db.value('SELECT qty FROM rc02.toast_child WHERE id=1;') == '2')
    finally:
        db.run('DROP SCHEMA rc02 CASCADE;', False)
        db.run('DROP ROLE IF EXISTS rc02_sql_app;', False)


if __name__ == '__main__':
    main()
