#!/usr/bin/env python3
"""PGJT-70 bounded resource, RLS, index-health and direct-growth regressions."""
import argparse
import json
from pathlib import Path
import selectors
import subprocess
import time

from security_contract_matrix import Database, quote


class Session:
    def __init__(self, db):
        self.process = subprocess.Popen(db.command + ['-q'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1)

    def send(self, sql):
        self.process.stdin.write(sql + '\n')
        self.process.stdin.flush()

    def ready(self):
        self.send("SELECT 'READY';")
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                if selector.select(max(0, deadline - time.monotonic())):
                    if self.process.stdout.readline().strip() == 'READY':
                        return
            raise AssertionError('database session did not reach the lock barrier')
        finally:
            selector.close()

    def finish(self, sql='COMMIT;'):
        self.send(sql)
        self.process.stdin.close()
        self.process.wait(timeout=10)
        assert self.process.returncode == 0, self.process.stderr.read()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)


def wait_for_lock(db, application):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if db.value("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name="
                    + quote(application) + " AND wait_event_type='Lock');") == 't':
            return
        time.sleep(.02)
    raise AssertionError('writer did not reach the root-lock barrier')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--psql', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    db = Database(args.psql, args.database)
    rows = []

    def record(name, ok):
        rows.append({'id': 'PGJT-70/' + name, 'status': 'PASS' if ok else 'FAIL'})
        args.result.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        assert ok, name

    def create(name='documents', **limits):
        spec = {'root': {'table': 'jt70.root', 'key': 'id', 'fields': {'label': 'label'},
                         'insert': True, 'update': True, 'delete': True},
                'array': {'name': 'items', 'table': 'jt70.item', 'key': 'id',
                          'parent_fk': 'parent', 'order_by': 'id',
                          'fields': {'id': 'id', 'label': 'label'},
                          'insert': True, 'update': True, 'delete': True},
                'limits': {'supplied_children': 3, 'stored_children': 3, **limits}}
        return db.run(f"SELECT jsontailor.create_duality_view('jt70.{name}',{quote(json.dumps(spec))}::jsonb);", False)

    db.run('DROP SCHEMA IF EXISTS jt70 CASCADE; DROP ROLE IF EXISTS jt70_app; '
           'CREATE ROLE jt70_app; CREATE SCHEMA jt70; '
           "CREATE TABLE jt70.root(id integer PRIMARY KEY,label text NOT NULL DEFAULT 'original'); "
           'CREATE TABLE jt70.item(id integer PRIMARY KEY,parent integer NOT NULL REFERENCES jt70.root(id),'
           "label text NOT NULL DEFAULT 'default'); INSERT INTO jt70.root VALUES(1,'original'),(2,'other'); "
           "INSERT INTO jt70.item(id,parent) VALUES(1,1),(2,1); ")
    writer = None
    try:
        result = create()
        assert result.returncode == 0, result.stderr
        record('missing-index-health', db.state(db.run('SELECT jt70.documents__check();', False)) == 'PT422')
        db.run('CREATE INDEX item_parent_order ON jt70.item(parent,id);')
        record('valid-index-health', db.value("SELECT jt70.documents__check()->>'status';") == 'OK')
        db.run('GRANT USAGE ON SCHEMA jt70 TO jt70_app; '
               'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA jt70 TO jt70_app;')
        record('reader-grant-required', db.state(db.run(
               'SET ROLE jt70_app; SELECT data FROM jt70.documents;', False)) == '42501')
        db.run("SELECT jsontailor.grant_array_reader('jt70.documents','jt70_app',true);")
        record('stored-n-minus-one', db.value("SET ROLE jt70_app; SELECT jsonb_array_length(data->'items') "
               'FROM jt70.documents WHERE _id=1;') == '2')
        db.run('INSERT INTO jt70.item(id,parent) VALUES(3,1);')
        record('stored-n', db.value("SET ROLE jt70_app; SELECT jsonb_array_length(data->'items') "
               'FROM jt70.documents WHERE _id=1;') == '3')
        db.run('INSERT INTO jt70.item(id,parent) VALUES(4,1);')
        record('stored-n-plus-one', db.state(db.run(
               'SET ROLE jt70_app; SELECT data FROM jt70.documents WHERE _id=1;', False)) == 'PT413')
        record('oversized-delete-preflight', db.state(db.run(
               "SELECT jt70.documents__delete_document(1,'irrelevant');", False)) == 'PT413'
               and db.value('SELECT count(*) FROM jt70.item WHERE parent=1;') == '4')
        db.run('INSERT INTO jt70.item(id,parent) VALUES(5,1),(6,1); '
               'ALTER TABLE jt70.item ENABLE ROW LEVEL SECURITY; '
               'CREATE POLICY visible_children ON jt70.item TO jt70_app USING(id<=2);')
        record('rls-visible-n-minus-one', db.value("SET ROLE jt70_app; SELECT jsonb_array_length(data->'items') "
               'FROM jt70.documents WHERE _id=1;') == '2')
        db.run('ALTER POLICY visible_children ON jt70.item USING(id<=3);')
        record('rls-visible-n', db.value("SET ROLE jt70_app; SELECT jsonb_array_length(data->'items') "
               'FROM jt70.documents WHERE _id=1;') == '3')
        db.run('ALTER POLICY visible_children ON jt70.item USING(id<=4);')
        record('rls-visible-n-plus-one', db.state(db.run(
               'SET ROLE jt70_app; SELECT data FROM jt70.documents WHERE _id=1;', False)) == 'PT413')
        db.run('ALTER POLICY visible_children ON jt70.item USING(id<=3);')
        db.run("SELECT jsontailor.regenerate_duality_view('jt70.documents');")
        record('reader-provenance-regeneration', db.value("SET ROLE jt70_app; SELECT jsonb_array_length(data->'items') "
               'FROM jt70.documents WHERE _id=1;') == '3')
        db.run('ALTER TABLE jt70.item DISABLE ROW LEVEL SECURITY; DELETE FROM jt70.item WHERE id>=3;')
        for size in (2, 3, 4):
            doc = {'_id': 10 + size, 'items': [{'id': 100 + i, 'label': 'new'} for i in range(size)]}
            result = db.run('BEGIN; INSERT INTO jt70.documents(data) VALUES('
                            + quote(json.dumps(doc)) + '::jsonb);ROLLBACK;', False)
            assert (result.returncode == 0) == (size <= 3), result.stderr
            if size > 3:
                assert db.state(result) == 'PT413', result.stderr
        record('supplied-boundaries-atomic', db.value('SELECT count(*) FROM jt70.root;') == '2')
        assert create('smallbytes', request_bytes=1024).returncode == 0
        result = db.run('INSERT INTO jt70.smallbytes(data) VALUES(' + quote(json.dumps({'label': 'x' * 1024})) + '::jsonb);', False)
        record('sql-document-bytes', db.state(result) == 'PT413')
        assert create('shallow', json_depth=2).returncode == 0
        result = db.run("INSERT INTO jt70.shallow(data) VALUES('{\"unknown\":{\"nested\":{}}}'::jsonb);", False)
        record('sql-document-depth', db.state(result) == 'PT413')
        bad = [{'stored_children': -1}, {'stored_children': 100001}, {'json_depth': 0},
               {'json_depth': 129}, {'request_bytes': 1023}, {'request_bytes': 16777217},
               {'supplied_children': '3'}, {'unknown': 3}]
        record('configuration-bounds', all(db.state(create('invalid', **limit)) == '22023' for limit in bad))
        db.run('INSERT INTO jt70.item(id,parent) SELECT 1000+g,2 FROM generate_series(1,50000)g; '
               'INSERT INTO jt70.item(id,parent) VALUES(3,1),(4,1); ANALYSE jt70.item;')
        plan = json.loads(db.run('EXPLAIN (ANALYSE,FORMAT JSON) SELECT count(*) FROM '
                                 '(SELECT 1 FROM jt70.item WHERE parent=1 LIMIT 4) bounded;').stdout)[0]['Plan']
        def nodes(node):
            yield node
            for child in node.get('Plans', []):
                yield from nodes(child)
        record('bounded-indexed-preflight-plan', any(n['Node Type'] == 'Limit' and n['Actual Rows'] == 4 for n in nodes(plan))
               and any('Index' in n['Node Type'] for n in nodes(plan)))
        db.run('DELETE FROM jt70.item WHERE parent=2 OR id>=3;')
        writer = Session(db)
        writer.send("BEGIN; SET statement_timeout='5s'; DO $$BEGIN PERFORM id FROM jt70.root WHERE id=1 FOR KEY SHARE;END$$;")
        writer.ready()
        updater = subprocess.Popen(db.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        updater.stdin.write("\\set VERBOSITY verbose\nSET application_name='jt70_growth_waiter'; "
                            "SET statement_timeout='5s';UPDATE jt70.documents SET "
                            "data=jsonb_set(data,'{label}','\"must-not-commit\"') WHERE _id=1;\n")
        updater.stdin.close()
        try:
            wait_for_lock(db, 'jt70_growth_waiter')
            writer.finish('INSERT INTO jt70.item(id,parent) VALUES(3,1),(4,1);COMMIT;')
            updater.wait(timeout=8)
            stderr = updater.stderr.read()
            record('concurrent-direct-source-growth', updater.returncode != 0 and 'PT413' in stderr
                   and db.value('SELECT label FROM jt70.root WHERE id=1;') == 'original'
                   and db.value('SELECT count(*) FROM jt70.item WHERE parent=1;') == '4')
        finally:
            if updater.poll() is None:
                updater.kill()
                updater.wait(timeout=5)
        db.run('CREATE TABLE jt70.partition_item(id integer PRIMARY KEY,parent integer REFERENCES jt70.root(id),label text) PARTITION BY RANGE(id); '
               'CREATE TABLE jt70.partition_low PARTITION OF jt70.partition_item FOR VALUES FROM(0) TO(100); '
               'CREATE INDEX partition_parent_order ON jt70.partition_item(parent,id);')
        spec = {'root': {'table': 'jt70.root', 'key': 'id', 'fields': {'label': 'label'}},
                'array': {'name': 'items', 'table': 'jt70.partition_item', 'key': 'id',
                          'parent_fk': 'parent', 'fields': {'id': 'id', 'label': 'label'}}}
        db.run("SELECT jsontailor.create_duality_view('jt70.partition_documents'," + quote(json.dumps(spec)) + '::jsonb);')
        record('partition-index-health', db.value("SELECT jt70.partition_documents__check()->>'status';") == 'OK')
        db.run('DROP INDEX jt70.partition_parent_order;')
        record('partition-missing-index-health', db.state(db.run('SELECT jt70.partition_documents__check();', False)) == 'PT422')
    finally:
        if writer:
            writer.close()
        db.run('DROP SCHEMA jt70 CASCADE; DROP ROLE jt70_app;')


if __name__ == '__main__':
    main()
