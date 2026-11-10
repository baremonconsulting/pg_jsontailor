#!/usr/bin/env python3
"""PGJT-90: qualify typed prefilter/keyset work, retain JSON-list diagnostics."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--psql', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    def sql(statement):
        result = subprocess.run([args.psql, '-XqAt', '-v', 'ON_ERROR_STOP=1',
            '-d', args.database], input=statement, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    # This suite requires a disposable test database, like the other DB suites.
    sql('''DROP SCHEMA IF EXISTS jt_read_qualification CASCADE;
CREATE SCHEMA jt_read_qualification;
CREATE TABLE jt_read_qualification.roots(id bigint PRIMARY KEY,status text NOT NULL);
CREATE TABLE jt_read_qualification.children(id bigint PRIMARY KEY,
 parent bigint NOT NULL REFERENCES jt_read_qualification.roots(id),label text NOT NULL);
CREATE INDEX ON jt_read_qualification.roots(status,id);
CREATE INDEX ON jt_read_qualification.children(parent,id);
INSERT INTO jt_read_qualification.roots
 SELECT g,CASE WHEN g=1000 THEN 'needle' ELSE 'ordinary' END FROM generate_series(1,1000) g;
INSERT INTO jt_read_qualification.children
 SELECT r.id*100+c,r.id,'child-'||c FROM jt_read_qualification.roots r CROSS JOIN generate_series(1,10) c;
ANALYZE jt_read_qualification.roots; ANALYZE jt_read_qualification.children;''')
    spec = {'root': {'table': 'jt_read_qualification.roots', 'key': 'id',
                     'fields': {'status': 'status'}},
            'array': {'name': 'items', 'table': 'jt_read_qualification.children',
                      'key': 'id', 'parent_fk': 'parent', 'order_by': 'id',
                      'fields': {'id': 'id', 'label': 'label'}}}
    encoded = json.dumps(spec).replace("'", "''")
    sql("SELECT jsontailor.create_duality_view('jt_read_qualification.documents','" + encoded + "'::jsonb);")
    health = json.loads(sql('SELECT jt_read_qualification.documents__check();'))
    if health['status'] != 'OK':
        raise RuntimeError('PGJT-90 fixture health failed')
    queries = {
        'identity': 'SELECT data FROM jt_read_qualification.documents WHERE _id=1000',
        'keyset': 'SELECT data FROM jt_read_qualification.documents WHERE _id>990 ORDER BY _id LIMIT 10',
        'typed-prefilter': "SELECT d.data FROM (SELECT id FROM jt_read_qualification.roots WHERE status='needle' ORDER BY id LIMIT 10) s JOIN jt_read_qualification.documents d ON d._id=s.id",
        'json-filter-diagnostic': "SELECT data FROM jt_read_qualification.documents WHERE data->>'status'='needle' LIMIT 10",
        'deep-offset-diagnostic': 'SELECT data FROM jt_read_qualification.documents ORDER BY _id OFFSET 990 LIMIT 10',
    }
    evidence = {}
    for name, query in queries.items():
        plan = json.loads(sql('EXPLAIN (ANALYZE,BUFFERS,TIMING OFF,FORMAT JSON) ' + query))[0]
        def aggregate_work(node):
            return ((node['Actual Loops'] if node['Node Type'] == 'Aggregate' else 0)
                    + sum(aggregate_work(child) for child in node.get('Plans', [])))
        evidence[name] = {'aggregate_loops': aggregate_work(plan['Plan']), 'plan': plan}
    expected_rows = json.loads(sql("SELECT jsonb_agg(x) FROM (" + queries['typed-prefilter'] + ') q(x)'))
    passed = (len(expected_rows) == 1 and expected_rows[0]['_id'] == 1000
              and len(expected_rows[0]['items']) == 10
              and evidence['identity']['aggregate_loops'] <= 2
              and evidence['keyset']['aggregate_loops'] <= 20
              and evidence['typed-prefilter']['aggregate_loops'] <= 2)
    result = {'name': 'PGJT-90/typed-prefilter-keyset',
              'status': 'PASS' if passed else 'FAIL', 'evidence': evidence,
              'purpose': 'query-shape qualification; not production timing evidence'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'evidence'}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
