#!/usr/bin/env python3
"""Prove that generated bulk writes do not allocate a subtransaction per row."""

import argparse
import json
import re
import subprocess
import time


EXPECTED_WORKLOADS = (
    "normal bulk INSERT", "explicit-child bulk INSERT",
    "normal bulk UPDATE", "explicit-child bulk UPDATE",
    "normal MERGE INSERT", "explicit-child MERGE INSERT",
    "normal MERGE UPDATE", "explicit-child MERGE UPDATE",
)


def run(psql: str, database: str, sql: str) -> str:
    result = subprocess.run(
        [psql, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=sql, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def run_unchecked(psql: str, database: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [psql, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-v",
         "VERBOSITY=verbose", "-d", database], input=sql, text=True,
        capture_output=True)


def is_positive_xid_noise(delta: int, expected_delta: int,
                          changed: int, expected_changed: int,
                          state_matches: bool = True) -> bool:
    return (state_matches and changed == expected_changed
            and delta > expected_delta)


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
    empty_items = "'items',jsonb_build_array()"
    explicit_items = ("'items',jsonb_build_array(jsonb_build_object("
                      "'id',100000+g,'value','child'))")
    insert_document = ("jsonb_build_object('_id',g,'value','insert-'||g,")
    merge_insert = (
        "MERGE INTO jt_subxid.documents target USING (SELECT g root_id," +
        insert_document + "{items}) doc FROM generate_series(1,{size})g) source "
        "ON target._id=source.root_id WHEN NOT MATCHED THEN "
        "INSERT(data) VALUES(source.doc)")
    workloads = [
        {"workload": "normal bulk INSERT", "seed": "", "statement":
         "INSERT INTO jt_subxid.documents(data) SELECT " + insert_document +
         empty_items + ") FROM generate_series(1,{size})g", "predicate":
         "data->>'value'='insert-'||_id::text AND jsonb_array_length(data->'items')=0"},
        {"workload": "explicit-child bulk INSERT", "seed": "", "statement":
         "INSERT INTO jt_subxid.documents(data) SELECT " + insert_document +
         explicit_items + ") FROM generate_series(1,{size})g", "predicate":
         "data->>'value'='insert-'||_id::text AND jsonb_array_length(data->'items')=1 "
         "AND (data->'items'->0->>'id')::integer=100000+_id "
         "AND data->'items'->0->>'value'='child'"},
        {"workload": "normal bulk UPDATE", "seed": "root", "statement":
         "UPDATE jt_subxid.documents SET data=jsonb_set(data,'{value}',"
         "to_jsonb('after-'||_id::text))", "predicate":
         "data->>'value'='after-'||_id::text AND jsonb_array_length(data->'items')=0"},
        {"workload": "explicit-child bulk UPDATE", "seed": "root", "statement":
         "UPDATE jt_subxid.documents SET data=jsonb_set(data,'{items}',"
         "jsonb_build_array(jsonb_build_object('id',100000+_id,'value','child')))",
         "predicate": "data->>'value'='before' AND jsonb_array_length(data->'items')=1 "
         "AND (data->'items'->0->>'id')::integer=100000+_id "
         "AND data->'items'->0->>'value'='child'"},
        {"workload": "normal MERGE INSERT", "seed": "", "statement":
         merge_insert.format(items=empty_items, size="{size}"), "predicate":
         "data->>'value'='insert-'||_id::text AND jsonb_array_length(data->'items')=0"},
        {"workload": "explicit-child MERGE INSERT", "seed": "", "statement":
         merge_insert.format(items=explicit_items, size="{size}"), "predicate":
         "data->>'value'='insert-'||_id::text AND jsonb_array_length(data->'items')=1 "
         "AND (data->'items'->0->>'id')::integer=100000+_id "
         "AND data->'items'->0->>'value'='child'"},
        {"workload": "normal MERGE UPDATE", "seed": "root", "statement":
         "MERGE INTO jt_subxid.documents target USING (SELECT _id root_id,"
         "jsonb_set(data,'{value}',to_jsonb('after-'||_id::text)) doc "
         "FROM jt_subxid.documents) source ON target._id=source.root_id "
         "WHEN MATCHED THEN UPDATE SET data=source.doc", "predicate":
         "data->>'value'='after-'||_id::text AND jsonb_array_length(data->'items')=0"},
        {"workload": "explicit-child MERGE UPDATE", "seed": "root", "statement":
         "MERGE INTO jt_subxid.documents target USING (SELECT _id root_id,"
         "jsonb_set(data,'{items}',jsonb_build_array(jsonb_build_object("
         "'id',100000+_id,'value','child'))) doc FROM jt_subxid.documents) source "
         "ON target._id=source.root_id WHEN MATCHED THEN UPDATE SET data=source.doc",
         "predicate": "data->>'value'='before' AND jsonb_array_length(data->'items')=1 "
         "AND (data->'items'->0->>'id')::integer=100000+_id "
         "AND data->'items'->0->>'value'='child'"},
    ]

    def measure(prepare: str, statement: str, predicate: str, state_sql: str,
                expected_delta: int, size: int, expected_children: int) -> dict:
        attempts = []
        for _ in range(3):
            run(args.psql, args.database, prepare)
            started = time.perf_counter()
            values = run(args.psql, args.database, f"""
BEGIN;
SELECT pg_current_xact_id();
{statement};
COMMIT;
SELECT pg_current_xact_id();
SELECT count(*) FROM {predicate};
{state_sql}
""").splitlines()
            elapsed = time.perf_counter() - started
            start, end, changed, roots, children = map(int, values[:5])
            attempts.append({"xid_delta": end - start, "changed": changed,
                             "roots": roots, "children": children,
                             "elapsed_seconds": round(elapsed, 6)})
            # Retry only positive global-XID noise. Data-state failures and
            # deltas below the operation contract are deterministic failures.
            if not is_positive_xid_noise(
                    end - start, expected_delta, changed, size,
                    roots == size and children == expected_children):
                break
        best = min(attempts, key=lambda item: item["xid_delta"])
        return {"xid_delta": best["xid_delta"], "changed": best["changed"],
                "roots": best["roots"], "children": best["children"],
                "expected_roots": size, "expected_children": expected_children,
                "elapsed_seconds": best["elapsed_seconds"],
                "microseconds_per_document": round(
                    best["elapsed_seconds"] * 1_000_000 / size, 3),
                "attempts": attempts}

    for case in workloads:
      for size in (100, 1000):
        prepare = "TRUNCATE jt_subxid.child,jt_subxid.root;" + (
            f"INSERT INTO jt_subxid.root SELECT g,'before' "
            f"FROM generate_series(1,{size})g;" if case["seed"] else "")
        statement = case["statement"].replace("{size}", str(size))
        measured = measure(
            prepare, statement,
            f"jt_subxid.documents WHERE {case['predicate']}",
            "SELECT count(*) FROM jt_subxid.root;"
            "SELECT count(*) FROM jt_subxid.child;",
            1, size, size if "explicit-child" in case["workload"] else 0)
        evidence.append({"workload": case["workload"], "documents": size,
                         "expected_xid_delta": 1, **measured})

    run(args.psql, args.database, """
DROP SCHEMA IF EXISTS jt_subxid_deferrable CASCADE;
CREATE SCHEMA jt_subxid_deferrable;
CREATE TABLE jt_subxid_deferrable.root(id integer PRIMARY KEY,value text NOT NULL);
CREATE TABLE jt_subxid_deferrable.child(
 id integer,parent integer NOT NULL REFERENCES jt_subxid_deferrable.root(id),value text,
 CONSTRAINT child_pkey PRIMARY KEY(id) DEFERRABLE INITIALLY IMMEDIATE,
 CONSTRAINT child_value_key UNIQUE(value));
SELECT jsontailor.create_duality_view(
  'jt_subxid_deferrable.documents',
  '{"root":{"table":"jt_subxid_deferrable.root","key":"id",\
"fields":{"value":"value"},"insert":true,"update":true,"delete":true},\
"array":{"name":"items","table":"jt_subxid_deferrable.child","key":"id",\
"parent_fk":"parent","fields":{"id":"id","value":"value"},\
"insert":true,"update":true,"delete":true}}');
""")
    for size in (100, 1000):
        measured = measure(
            "TRUNCATE jt_subxid_deferrable.child,jt_subxid_deferrable.root;"
            f"INSERT INTO jt_subxid_deferrable.root SELECT g,'before' "
            f"FROM generate_series(1,{size})g;",
            "UPDATE jt_subxid_deferrable.documents "
            "SET data=jsonb_set(data,'{items}',jsonb_build_array(jsonb_build_object("
            "'id',100000+_id,'value','child-'||_id::text)))",
            "jt_subxid_deferrable.documents WHERE "
            "data->>'value'='before' AND jsonb_array_length(data->'items')=1 "
            "AND (data->'items'->0->>'id')::integer=100000+_id "
            "AND data->'items'->0->>'value'='child-'||_id::text",
            "SELECT count(*) FROM jt_subxid_deferrable.root;"
            "SELECT count(*) FROM jt_subxid_deferrable.child;",
            size + 1, size, size)
        evidence.append({
            "workload": "DEFERRABLE explicit-child compatibility UPDATE",
            "documents": size, "expected_xid_delta": size + 1, **measured})

    run(args.psql, args.database, """
TRUNCATE jt_subxid_deferrable.child,jt_subxid_deferrable.root;
INSERT INTO jt_subxid_deferrable.root VALUES(1,'one'),(2,'two');
""")
    conflict = run_unchecked(args.psql, args.database, """
UPDATE jt_subxid_deferrable.documents
SET data=jsonb_set(data,'{items}',jsonb_build_array(jsonb_build_object(
  'id',900+_id,'value','duplicate')));
""")
    match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", conflict.stderr)
    constraint_match = re.search(
        r"CONSTRAINT NAME:\s+([^\s]+)", conflict.stderr)
    diagnostic = {
        "workload": "DEFERRABLE explicit-child native conflict diagnostic",
        "command_failed": conflict.returncode != 0,
        "sqlstate": match.group(1) if match else None,
        "constraint": constraint_match.group(1) if constraint_match else None,
        "rolled_back_children": int(run(
            args.psql, args.database,
            "SELECT count(*) FROM jt_subxid_deferrable.child;"))}

    expected_matrix = {
        (workload, size) for workload in EXPECTED_WORKLOADS for size in (100, 1000)}
    expected_matrix |= {
        ("DEFERRABLE explicit-child compatibility UPDATE", size)
        for size in (100, 1000)}
    observed_matrix = {(item["workload"], item["documents"]) for item in evidence}
    matrix_complete = observed_matrix == expected_matrix and len(evidence) == 18
    passed = matrix_complete and all(
                 item["xid_delta"] == item["expected_xid_delta"]
                 and item["changed"] == item["documents"]
                 and item["roots"] == item["expected_roots"]
                 and item["children"] == item["expected_children"]
                 for item in evidence) and diagnostic == {
                     "workload": "DEFERRABLE explicit-child native conflict diagnostic",
                     "command_failed": True,
                     "sqlstate": "23505", "constraint": "child_value_key",
                     "rolled_back_children": 0}
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"name": "PGJT-42 bulk operation matrix uses one top-level XID",
                                 "status": "PASS" if passed else "FAIL",
                                 "observed": {"workloads": evidence,
                                              "native_diagnostics": diagnostic,
                                              "matrix_complete": matrix_complete},
                                 "environment": {"postgres": postgres}},
                                sort_keys=True) + "\n")
    print(json.dumps({"workloads": evidence, "native_diagnostics": diagnostic,
                      "matrix_complete": matrix_complete},
                     sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
