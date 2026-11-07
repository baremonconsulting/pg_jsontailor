#!/usr/bin/env python3
"""Export candidate DDL and exercise it without extension or runtime schema."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

from dump_restore import Commands, literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from export_standalone import export
from verify_generated_sql_notice import verify_bundle

RESULT_IDS = (
    "PGJT-68/export-candidate", "PGJT-68/clean-runtime", "PGJT-68/crud-etags",
    "PGJT-68/direct-rpc-delete", "PGJT-68/merge", "PGJT-68/health-binding-guards",
    "PGJT-68/rls-acl", "PGJT-68/custom-restore", "PGJT-68/plain-restore",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def last(commands: Commands, database: str, sql: str) -> str:
    return commands.sql(database, sql).splitlines()[-1]


def absent(commands: Commands, database: str) -> bool:
    return last(commands, database, """SELECT
 NOT EXISTS(SELECT FROM pg_extension WHERE extname='pg_jsontailor')
 AND NOT EXISTS(SELECT FROM pg_namespace WHERE nspname='jsontailor');""") == "t"


def crud(commands: Commands, database: str, ident: int = 301) -> None:
    result = last(commands, database, f"""
INSERT INTO api.orders_dv(data) VALUES(
 '{{"_id":{ident},"status":"standalone","created":"2026-11-03",
    "customer":{{"customerId":7}},
    "items":[{{"product":"child","qty":1,"price":2}}]}}');
CREATE TEMP TABLE before_write AS SELECT data FROM api.orders_dv WHERE _id={ident};
UPDATE api.orders_dv SET data=jsonb_set(data,'{{status}}','"changed"') WHERE _id={ident};
SELECT (SELECT data->>'status'='changed' AND data#>>'{{_metadata,etag}}' ~ '^[0-9A-F]{{32}}$'
 AND data#>>'{{_metadata,etag}}'<>(SELECT data#>>'{{_metadata,etag}}' FROM before_write)
 FROM api.orders_dv WHERE _id={ident})
 AND EXISTS(SELECT FROM shop.order_items WHERE order_id={ident});""")
    require(result == "t", "standalone CRUD/ETag state differs")


def deletes(commands: Commands, database: str, ident: int = 301) -> None:
    require(last(commands, database, f"""
WITH removed AS (DELETE FROM api.orders_dv WHERE _id={ident}
 AND data#>>'{{_metadata,etag}}'='stale' RETURNING _id)
SELECT count(*)=0 FROM removed;""") == "t", "stale predicate removed a row")
    require(last(commands, database, f"""
WITH removed AS (DELETE FROM api.orders_dv WHERE _id={ident}
 AND data#>>'{{_metadata,etag}}'=(SELECT data#>>'{{_metadata,etag}}'
 FROM api.orders_dv WHERE _id={ident}) RETURNING _id)
SELECT count(*)=1 FROM removed;""") == "t", "direct delete did not remove one document")
    commands.sql(database, f"""INSERT INTO api.orders_dv(data) VALUES(
 '{{"_id":{ident},"status":"rpc","created":"2026-11-03","customer":null,"items":[]}}');""")
    require(last(commands, database, f"""
SELECT api.orders_dv__delete_document({ident},
 (SELECT data#>>'{{_metadata,etag}}' FROM api.orders_dv WHERE _id={ident}))->>'status'='rpc';
SELECT NOT EXISTS(SELECT FROM shop.orders WHERE order_id={ident})
 AND NOT EXISTS(SELECT FROM shop.order_items WHERE order_id={ident});""") == "t",
            "RPC delete left relational state")


def merges(commands: Commands, database: str) -> None:
    require(last(commands, database, """
MERGE INTO api.orders_dv d USING (VALUES (302,
 '{"_id":302,"status":"merge","created":"2026-11-03","customer":null,"items":[]}'::jsonb))s(id,data)
ON d._id=s.id WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data);
MERGE INTO api.orders_dv d USING (SELECT 302 id,jsonb_set(data,'{status}','"merged"') data
 FROM api.orders_dv WHERE _id=302)s ON d._id=s.id
WHEN MATCHED THEN UPDATE SET data=s.data;
SELECT status='merged' FROM shop.orders WHERE order_id=302;
""") == "t", "MERGE insert/update state differs")
    require(last(commands, database, """
MERGE INTO api.orders_dv d USING (SELECT 302 id,data FROM api.orders_dv WHERE _id=302)s
ON d._id=s.id WHEN MATCHED AND d.data#>>'{_metadata,etag}'=s.data#>>'{_metadata,etag}' THEN DELETE;
SELECT NOT EXISTS(SELECT FROM shop.orders WHERE order_id=302);""") == "t", "MERGE delete failed")


def failed_sql(commands: Commands, database: str, sql: str, expected: str) -> None:
    result = subprocess.run([str(commands.pg_bin / "psql"), "-XAt", "-v", "ON_ERROR_STOP=1",
                             "-d", database], input="\\set VERBOSITY verbose\n" + sql,
                            text=True, capture_output=True, timeout=60)
    require(result.returncode != 0 and re.search(r"ERROR:\s+" + expected + ":", result.stderr)
            is not None, f"expected {expected}, got {result.stderr}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commands = Commands(args.pg_bin.resolve(), args.maintenance_database)
    major = last(commands, args.maintenance_database, "SHOW server_version_num;")[:2]
    require(major in {"17", "18", "19"}, "unsupported PostgreSQL major")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    prefix = "pgjt68_" + uuid.uuid4().hex[:10]
    databases = [prefix + suffix for suffix in ("_source", "_target", "_custom", "_plain")]
    source, target, custom, plain = databases
    role = prefix + "_reader"
    results: list[dict] = []
    ddl_hash = ""
    created: list[str] = []

    def record(name: str, passed: bool, observed: str = "") -> None:
        results.append({"name": name, "status": "PASS" if passed else "FAIL",
                        "postgresql": major, "source_commit": sha, "dirty_tree": dirty,
                        "generated_sql_sha256": ddl_hash, "observed": observed})
        require(passed, name + ": " + observed)

    with tempfile.TemporaryDirectory(prefix="pgjt68-bundle-") as temporary:
        folder = Path(temporary)
        try:
            commands.create(source)
            created.append(source)
            commands.sql(source, "CREATE EXTENSION pg_jsontailor;")
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", source,
                         "-f", str(ROOT / "examples/shop_schema.sql"))
            commands.sql(source, "CREATE SCHEMA api;")
            # The installed generator must exactly match this candidate's SQL body.
            candidate = (ROOT / "sql/pg_jsontailor--0.2.0.sql").read_text().split(
                "AS $generator$\n", 1)[1].split("\n$generator$;", 1)[0]
            installed = commands.sql(source, "SELECT prosrc FROM pg_proc WHERE oid="
                                     "'jsontailor.duality_view_ddl(text,jsonb)'::regprocedure;")
            require(installed == candidate.strip(), "installed generator differs from the candidate")
            bundle = folder / "bundle"
            export(str(commands.pg_bin / "psql"), source, "api.orders_dv",
                   ROOT / "examples/orders_dv.spec.json", bundle)
            ddl_hash = hashlib.sha256((bundle / "generated.sql").read_bytes()).hexdigest()
            verify_bundle(bundle)
            record(RESULT_IDS[0], True, "candidate generator body and exported SQL validated")
            commands.create(target)
            created.append(target)
            require(absent(commands, target), "target is not clean before import")
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", target,
                         "-f", str(ROOT / "examples/shop_schema.sql"))
            commands.sql(target, "CREATE SCHEMA api;")
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "--single-transaction",
                         "-d", target, "-f", str(bundle / "generated.sql"))
            record(RESULT_IDS[1], absent(commands, target), "no extension and no jsontailor schema")
            crud(commands, target)
            record(RESULT_IDS[2], True)
            deletes(commands, target)
            record(RESULT_IDS[3], True)
            merges(commands, target)
            record(RESULT_IDS[4], True)
            require(last(commands, target, "SELECT api.orders_dv__check()->>'status';") == "OK",
                    "health check failed")
            failed_sql(commands, target, "BEGIN; ALTER TABLE shop.orders RENAME COLUMN status TO state;"
                       "UPDATE api.orders_dv SET data=data WHERE _id=42;COMMIT;", "PT422")
            record(RESULT_IDS[5], last(commands, target,
                   "SELECT status='new' FROM shop.orders WHERE order_id=42;") == "t")
            commands.sql(target, f"""CREATE ROLE {role} NOLOGIN;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA api FROM PUBLIC;
GRANT USAGE ON SCHEMA api,shop TO {role};
GRANT SELECT,UPDATE,DELETE ON api.orders_dv TO {role};
GRANT SELECT ON shop.orders,shop.customers,shop.order_items TO {role};
GRANT EXECUTE ON FUNCTION api.orders_dv__delete_document(bigint,text) TO {role};""")
            failed_sql(commands, target, f"SET ROLE {role};UPDATE api.orders_dv SET data=data WHERE _id=42;", "42501")
            commands.sql(target, f"""GRANT UPDATE,DELETE ON shop.orders,shop.order_items TO {role};
ALTER TABLE shop.order_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY visible_child ON shop.order_items TO {role} USING(item_id<>2) WITH CHECK(item_id<>2);""")
            require(last(commands, target, f"SET ROLE {role};SELECT jsonb_array_length(data->'items')=1"
                         " FROM api.orders_dv WHERE _id=42;") == "t", "RLS child visibility differs")
            visible_etag = last(commands, target, f"SET ROLE {role};SELECT data#>>'{{_metadata,etag}}'"
                                " FROM api.orders_dv WHERE _id=42;")
            owner_etag = last(commands, target, "SELECT data#>>'{_metadata,etag}'"
                              " FROM api.orders_dv WHERE _id=42;")
            require(visible_etag != owner_etag, "ETag is not visibility-relative under RLS")
            commands.sql(target, f"SET ROLE {role};UPDATE api.orders_dv SET data="
                         "jsonb_set(data,'{status}','\"rls\"') WHERE _id=42;")
            failed_sql(commands, target, f"SET ROLE {role};DELETE FROM api.orders_dv WHERE _id=42;", "23503")
            commands.sql(target, f"REVOKE EXECUTE ON FUNCTION api.orders_dv__delete_document(bigint,text) FROM {role};")
            failed_sql(commands, target, f"SET ROLE {role};SELECT api.orders_dv__delete_document(42,'stale');", "42501")
            record(RESULT_IDS[6], last(commands, target, """SELECT
 (SELECT count(*)=2 FROM shop.order_items WHERE order_id=42)
 AND (SELECT status='rls' FROM shop.orders WHERE order_id=42)
 AND NOT EXISTS(SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='api' AND p.prosecdef);""") == "t")
            for restore_format, database, result_id in (("custom", custom, RESULT_IDS[7]),
                                                         ("plain", plain, RESULT_IDS[8])):
                archive = folder / (restore_format + ".dump")
                commands.run("pg_dump", "--format=" + restore_format, "--file", str(archive), target)
                commands.create(database)
                created.append(database)
                if restore_format == "custom":
                    commands.run("pg_restore", "--exit-on-error", "--dbname", database, str(archive))
                else:
                    commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database, "-f", str(archive))
                require(absent(commands, database), "restore introduced an extension dependency")
                require(last(commands, database, "SELECT api.orders_dv__check()->>'status';") == "OK",
                        "restored binding health differs")
                crud(commands, database)
                deletes(commands, database)
                merges(commands, database)
                record(result_id, last(commands, database, f"SET ROLE {role};SELECT "
                       "jsonb_array_length(data->'items')=1 FROM api.orders_dv WHERE _id=42;") == "t")
            require([row["name"] for row in results] == list(RESULT_IDS), "result inventory differs")
        finally:
            for database in reversed(created):
                commands.drop(database)
            commands.sql(args.maintenance_database, f"DROP ROLE IF EXISTS {role};")
            args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results))
    print("standalone generated SQL: PASS (PostgreSQL " + major + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
