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


def verify_restored(commands: Commands, database: str) -> None:
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
""").splitlines()
    if observed != ["0.2.0", "1:1", "true:false:dump restore health", "UPDATE 1", "t"]:
        raise RuntimeError(f"restored contract differs in {database}: {observed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--database-prefix", default="jsontailor_dump_restore")
    args = parser.parse_args()

    prefix = identifier(args.database_prefix)
    source = identifier(prefix + "_source")
    custom_target = identifier(prefix + "_custom")
    plain_target = identifier(prefix + "_plain")
    commands = Commands(args.pg_bin, args.maintenance_database)
    root = Path(__file__).resolve().parents[1]
    specification = json.loads((root / "examples/orders_dv.spec.json").read_text())

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
""")

            commands.run("pg_dump", "--format=custom", "--file", str(archive), source)
            commands.run("pg_dump", "--format=plain", "--file", str(plain), source)

            commands.create(custom_target)
            commands.run("pg_restore", "--exit-on-error", "--dbname", custom_target, str(archive))
            verify_restored(commands, custom_target)

            commands.create(plain_target)
            commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", plain_target,
                         "-f", str(plain))
            verify_restored(commands, plain_target)
        finally:
            for database in (plain_target, custom_target, source):
                commands.drop(database)

    print("custom and plain dump/restore: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
