#!/usr/bin/env python3
"""Verify custom and plain dump/restore of generated objects."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", value):
        raise ValueError(f"unsafe database name: {value!r}")
    return value


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Commands:
    def __init__(self, pg_bin: Path, maintenance: str):
        self.pg_bin = pg_bin
        self.maintenance = identifier(maintenance)

    def run(self, name: str, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(self.pg_bin / name), *args], input=input_text,
            text=True, capture_output=True, env=os.environ.copy())
        if result.returncode:
            raise RuntimeError(
                f"{name} failed ({result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        return result

    def sql(self, database: str, sql: str) -> str:
        return self.run("psql", "-XAt", "-v", "ON_ERROR_STOP=1", "-d", identifier(database),
                        input_text=sql).stdout.strip()

    def drop(self, database: str) -> None:
        self.run("dropdb", "--if-exists", "--force", "--maintenance-db",
                 self.maintenance, identifier(database))

    def create(self, database: str) -> None:
        self.run("createdb", "--maintenance-db", self.maintenance,
                 identifier(database))


def verify_restored(commands: Commands, database: str, restore_format: str) -> dict[str, str]:
    observed = commands.sql(database, """
SELECT extversion FROM pg_extension WHERE extname='pg_jsontailor';
SELECT count(*)||':'||count(*) FILTER (
  WHERE health_status='OK' AND generator_hash=current_generator_hash)
FROM jsontailor.duality_view_inventory();
SELECT has_table_privilege('public','api.orders_dv','SELECT')::text||':'||
       has_function_privilege('public','api.orders_dv__delete_document(bigint,text)','EXECUTE')::text||':'||
       coalesce(obj_description('api.orders_dv__check()'::regprocedure,'pg_proc'),'');
UPDATE api.orders_dv SET data=data WHERE _id=42;
SELECT (data#>>'{_metadata,etag}') IS NOT NULL FROM api.orders_dv WHERE _id=42;
SELECT string_agg(parent||':'||members,',' ORDER BY parent) FROM (
 SELECT p.relname parent,count(*) members
 FROM (VALUES('part_detail'),('part_root'),('part_item')) names(name)
 JOIN pg_class p ON p.relname=names.name
 JOIN pg_namespace n ON n.oid=p.relnamespace AND n.nspname='partition_restore'
 CROSS JOIN LATERAL pg_partition_tree(p.oid) tree
 GROUP BY p.relname) topology;
SELECT (data#>>'{label}')||':'||(data#>>'{detail,label}')||':'||
       (data#>>'{items,0,label}')
 FROM api.partition_dv WHERE _id=150;
SELECT api.partition_dv__check() IS NOT NULL;
UPDATE api.partition_dv SET data=jsonb_set(data,'{label}','"pre-regen-write"')
 WHERE _id=150;
SELECT label||':'||tableoid::regclass::text FROM partition_restore.part_root WHERE id=150;
UPDATE api.partition_dv SET data=jsonb_set(data,'{label}','"restored-update"')
 WHERE _id=150;
MERGE INTO api.partition_dv d USING (VALUES(151,
 '{"_id":151,"label":"merge-insert","detail":{"id":107},
   "items":[{"id":151,"label":"merge-item"}]}'::jsonb))s(id,data)
 ON d._id=s.id WHEN MATCHED THEN DO NOTHING
 WHEN NOT MATCHED THEN INSERT(data) VALUES(s.data);
MERGE INTO api.partition_dv d USING (SELECT 151 id,
 jsonb_set(data,'{label}','"merge-update"') data FROM api.partition_dv WHERE _id=151)s
 ON d._id=s.id WHEN MATCHED THEN UPDATE SET data=s.data;
SELECT (SELECT label FROM partition_restore.part_root WHERE id=150)||':'||
 (SELECT label FROM partition_restore.part_root WHERE id=151)||':'||
 (SELECT tableoid::regclass::text FROM partition_restore.part_root WHERE id=151)||':'||
 (SELECT tableoid::regclass::text FROM partition_restore.part_item WHERE id=151);
DELETE FROM api.partition_dv WHERE _id=150 AND data#>>'{_metadata,etag}'=
 (SELECT data#>>'{_metadata,etag}' FROM api.partition_dv WHERE _id=150);
MERGE INTO api.partition_dv d USING (SELECT 151 id,data FROM api.partition_dv WHERE _id=151)s
 ON d._id=s.id WHEN MATCHED AND d.data#>>'{_metadata,etag}'=
 s.data#>>'{_metadata,etag}' THEN DELETE;
INSERT INTO api.partition_dv(data) VALUES(
 '{"_id":152,"label":"rpc-delete","detail":{"id":107},
   "items":[{"id":152,"label":"rpc-item"}]}'::jsonb);
SELECT (api.partition_dv__delete_document(152,
 (SELECT data#>>'{_metadata,etag}' FROM api.partition_dv WHERE _id=152))->>'label')
 = 'rpc-delete';
SELECT (SELECT count(*) FROM partition_restore.part_root WHERE id IN(150,151,152))
 ||':'||(SELECT count(*) FROM partition_restore.part_item WHERE id IN(150,151,152));
SELECT jsontailor.regenerate_duality_view('api.partition_dv');
SELECT health_status||':'||(generator_hash=current_generator_hash)::text
 FROM jsontailor.duality_view_inventory() WHERE view_name='api.partition_dv';
""").splitlines()
    expected = ["0.2.0", "2:2", "true:false:dump restore health", "UPDATE 1", "t",
                "part_detail:3,part_item:3,part_root:3",
                "partition-root:partition-detail:partition-item", "t", "UPDATE 1",
                "pre-regen-write:partition_restore.part_root_high", "UPDATE 1",
                "MERGE 1", "MERGE 1",
                "restored-update:merge-update:partition_restore.part_root_high:partition_restore.part_item_high",
                "DELETE 1", "MERGE 1", "INSERT 0 1", "t", "0:0", "", "OK:true"]
    if observed != expected:
        raise RuntimeError(f"restored contract differs in {database}: {observed}")
    return {"name": f"PGJT-47/partition-{restore_format}-restore", "status": "PASS",
            "observed": f"database={database},inventory=2:2,topology=3/3/3,crud=PASS"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--database-prefix", default="jsontailor_dump_restore")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    prefix = identifier(args.database_prefix)
    source = identifier(prefix + "_source")
    custom_target = identifier(prefix + "_custom")
    plain_target = identifier(prefix + "_plain")
    commands = Commands(args.pg_bin, args.maintenance_database)
    root = Path(__file__).resolve().parents[1]
    specification = json.loads((root / "examples/orders_dv.spec.json").read_text())
    partition_specification = {
        "root": {"table": "partition_restore.part_root", "key": "id",
                 "fields": {"label": "label"}, "insert": True,
                 "update": True, "delete": True},
        "object": {"name": "detail", "table": "partition_restore.part_detail",
                   "key": "id", "root_fk": "detail_id",
                   "fields": {"id": "id", "label": "label"}, "etag": True},
        "array": {"name": "items", "table": "partition_restore.part_item",
                  "key": "id", "parent_fk": "parent",
                  "fields": {"id": "id", "label": "label"},
                  "insert": True, "update": True, "delete": True}}
    results: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="pg-jsontailor-dump-") as temporary:
        archive = Path(temporary) / "database.dump"
        plain = Path(temporary) / "database.sql"
        try:
            for database in (custom_target, plain_target, source):
                commands.drop(database)
            commands.create(source)
            commands.sql(source, "CREATE EXTENSION pg_jsontailor;")
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", source,
                         "-f", str(root / "examples/shop_schema.sql"))
            commands.sql(source, """
ALTER TABLE shop.orders ADD COLUMN restore_history text;
ALTER TABLE shop.orders DROP COLUMN restore_history;
CREATE SCHEMA api;
SELECT jsontailor.create_duality_view('api.orders_dv',
""" + literal(json.dumps(specification, separators=(",", ":"))) + """::jsonb);
GRANT SELECT ON api.orders_dv TO PUBLIC;
REVOKE EXECUTE ON FUNCTION api.orders_dv__delete_document(bigint,text) FROM PUBLIC;
COMMENT ON FUNCTION api.orders_dv__check() IS 'dump restore health';
SELECT api.orders_dv__check();
CREATE SCHEMA partition_restore;
CREATE TABLE partition_restore.part_detail(id integer PRIMARY KEY,label text NOT NULL)
 PARTITION BY RANGE(id);
CREATE TABLE partition_restore.part_detail_low PARTITION OF partition_restore.part_detail
 FOR VALUES FROM(0) TO(100);
CREATE TABLE partition_restore.part_detail_high PARTITION OF partition_restore.part_detail
 FOR VALUES FROM(100) TO(200);
CREATE TABLE partition_restore.part_root(id integer PRIMARY KEY,
 detail_id integer REFERENCES partition_restore.part_detail(id),label text NOT NULL)
 PARTITION BY RANGE(id);
CREATE TABLE partition_restore.part_root_low PARTITION OF partition_restore.part_root
 FOR VALUES FROM(0) TO(100);
CREATE TABLE partition_restore.part_root_high PARTITION OF partition_restore.part_root
 FOR VALUES FROM(100) TO(200);
CREATE TABLE partition_restore.part_item(id integer PRIMARY KEY,
 parent integer NOT NULL REFERENCES partition_restore.part_root(id),label text NOT NULL)
 PARTITION BY RANGE(id);
CREATE TABLE partition_restore.part_item_low PARTITION OF partition_restore.part_item
 FOR VALUES FROM(0) TO(100);
CREATE TABLE partition_restore.part_item_high PARTITION OF partition_restore.part_item
 FOR VALUES FROM(100) TO(200);
INSERT INTO partition_restore.part_detail VALUES(107,'partition-detail');
INSERT INTO partition_restore.part_root VALUES(150,107,'partition-root');
INSERT INTO partition_restore.part_item VALUES(150,150,'partition-item');
SELECT jsontailor.create_duality_view('api.partition_dv',
""" + literal(json.dumps(partition_specification, separators=(",", ":"))) + """::jsonb);
SELECT api.partition_dv__check();
""")

            commands.run("pg_dump", "--format=custom", "--file", str(archive), source)
            commands.run("pg_dump", "--format=plain", "--file", str(plain), source)

            commands.create(custom_target)
            commands.run("pg_restore", "--exit-on-error", "--dbname", custom_target, str(archive))
            results.append(verify_restored(commands, custom_target, "custom"))

            commands.create(plain_target)
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", plain_target,
                         "-f", str(plain))
            results.append(verify_restored(commands, plain_target, "plain"))
        finally:
            for database in (plain_target, custom_target, source):
                commands.drop(database)

    if args.output:
        args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n"
                                       for row in results))
    print("custom and plain dump/restore: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
