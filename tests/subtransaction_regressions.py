#!/usr/bin/env python3
"""Prove that generated bulk writes do not allocate a subtransaction per row."""

import argparse
import json
import subprocess
import time


def run(psql: str, database: str, sql: str) -> str:
    result = subprocess.run(
        [psql, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    run(args.psql, args.database, """
DROP SCHEMA IF EXISTS jt_subxid CASCADE;
CREATE SCHEMA jt_subxid;
CREATE TABLE jt_subxid.root(id integer PRIMARY KEY,value text NOT NULL);
CREATE TABLE jt_subxid.child(id integer PRIMARY KEY,parent integer NOT NULL REFERENCES jt_subxid.root(id),value text);
SELECT jsontailor.create_duality_view(
  'jt_subxid.documents',
  '{"root":{"table":"jt_subxid.root","key":"id",\
"fields":{"value":"value"},"insert":true,"update":true,"delete":true},\
"array":{"name":"items","table":"jt_subxid.child","key":"id",\
"parent_fk":"parent","fields":{"id":"id","value":"value"},\
"insert":true,"update":true,"delete":true}}');
""")

    evidence = []
    postgres = run(args.psql, args.database, "SELECT version();")
    workloads = {
        "root-only bulk UPDATE":
            "SET data=jsonb_set(data,'{value}',to_jsonb('after-'||_id::text))",
        "explicit-child append bulk UPDATE":
            "SET data=jsonb_set(data,'{items}',jsonb_build_array(jsonb_build_object(" 
            "'id',100000+_id,'value','child')))"
    }
    for workload, assignment in workloads.items():
      for size in (100, 1000):
        workload_literal = "'" + workload.replace("'", "''") + "'"
        run(args.psql, args.database,
            "TRUNCATE jt_subxid.child,jt_subxid.root;"
            f"INSERT INTO jt_subxid.root SELECT g,'before' FROM generate_series(1,{size})g;")
        started = time.perf_counter()
        values = run(args.psql, args.database, f"""
BEGIN;
SELECT pg_current_xact_id();
UPDATE jt_subxid.documents
{assignment};
COMMIT;
SELECT pg_current_xact_id();
SELECT count(*) FROM jt_subxid.documents WHERE
  CASE WHEN {workload_literal}='root-only bulk UPDATE'
       THEN data->>'value'='after-'||_id::text
       ELSE jsonb_array_length(data->'items')=1 END;
""").splitlines()
        elapsed = time.perf_counter() - started
        start, end, changed = int(values[0]), int(values[1]), int(values[2])
        delta = end - start
        evidence.append({"workload": workload, "documents": size, "xid_delta": delta,
                         "changed": changed, "elapsed_seconds": round(elapsed, 6),
                         "microseconds_per_document": round(elapsed * 1_000_000 / size, 3)})

    passed = all(item["xid_delta"] == 1 and item["changed"] == item["documents"]
                 for item in evidence)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"name": "bulk document writes use one top-level XID",
                                 "status": "PASS" if passed else "FAIL",
                                 "observed": evidence,
                                 "environment": {"postgres": postgres}},
                                sort_keys=True) + "\n")
    print(json.dumps(evidence, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
