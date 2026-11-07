#!/usr/bin/env python3
"""Execute documented regeneration and exact-identity mapped-rename procedures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import uuid

from dump_restore import Commands, literal

ROOT = Path(__file__).resolve().parents[1]
RESULT_IDS = ("PGJT-66/name-preserving-regeneration", "PGJT-66/mapped-rename-recreation",
              "PGJT-66/dependency-rollback", "PGJT-66/acl-rls-overload-preservation")


def recreation() -> str:
    blocks = (ROOT / "docs/UPGRADING.md").read_text().split("```sql\n")
    return next(block.split("```", 1)[0] for block in blocks[1:]
                if "ALTER TABLE shop.orders RENAME COLUMN" in block)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    db = "pgjt66_" + uuid.uuid4().hex[:12]
    role = db + "_reader"
    commands = Commands(args.pg_bin, args.maintenance_database)
    results = []

    def record(name: str, passed: bool) -> None:
        results.append({"name": name, "status": "PASS" if passed else "FAIL"})
        if not passed:
            raise RuntimeError(name + " failed")

    commands.create(db)
    try:
        commands.sql(db, "CREATE EXTENSION pg_jsontailor;")
        commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", db,
                     "-f", str(ROOT / "examples/shop_schema.sql"))
        spec = json.loads((ROOT / "examples/orders_dv.spec.json").read_text())
        commands.sql(db, "CREATE SCHEMA api; SELECT jsontailor.create_duality_view("
                     "'api.orders_dv'," + literal(json.dumps(spec)) + "::jsonb);")
        commands.sql(db, f"""CREATE ROLE {role} NOLOGIN;
CREATE FUNCTION api.orders_dv__check(integer) RETURNS integer LANGUAGE sql AS 'SELECT $1';
GRANT USAGE ON SCHEMA api,shop TO {role};
GRANT SELECT ON api.orders_dv,shop.orders,shop.customers,shop.order_items TO {role};
ALTER TABLE shop.orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY visible ON shop.orders FOR SELECT TO {role} USING(order_id=42);
INSERT INTO shop.orders(order_id,status) VALUES(43,'hidden');
ALTER TABLE shop.orders RENAME CONSTRAINT orders_pkey TO renamed_orders_key;
SELECT jsontailor.regenerate_duality_view('api.orders_dv');""")
        record(RESULT_IDS[0], commands.sql(db, "SELECT api.orders_dv__check()->>'status';") == "OK")
        commands.sql(db, "CREATE VIEW api.consumer AS SELECT * FROM api.orders_dv;")
        spec["root"]["fields"]["status"] = "state"
        script = "\\set orders_dv_spec " + literal(json.dumps(spec)) + "\n" + recreation()
        # ON_ERROR_STOP disconnects and PostgreSQL rolls the failed transaction back.
        failed = subprocess.run([str(args.pg_bin / "psql"), "-XAt", "-v", "ON_ERROR_STOP=1",
                                 "-d", db], input="\\set VERBOSITY verbose\n" + script,
                                text=True, capture_output=True, timeout=60)
        dependency_failed = failed.returncode != 0 and "2BP01" in failed.stderr
        if not dependency_failed:
            raise RuntimeError("dependent view did not block transactional recreation")
        rollback_intact = (
            commands.sql(db, "SELECT status FROM shop.orders WHERE order_id=42;") == "new"
            and commands.sql(db, "SELECT count(*) FROM api.consumer;") == "2")
        commands.sql(db, "DROP VIEW api.consumer;")
        invalid = script.replace('"status": "state"', '"status": "absent_column"')
        failed = subprocess.run([str(args.pg_bin / "psql"), "-XAt", "-v", "ON_ERROR_STOP=1",
                                 "-d", db], input="\\set VERBOSITY verbose\n" + invalid,
                                text=True, capture_output=True, timeout=60)
        record(RESULT_IDS[2], rollback_intact and failed.returncode != 0 and "22023" in failed.stderr
               and commands.sql(db, "SELECT status FROM shop.orders WHERE order_id=42;") == "new"
               and commands.sql(db, "SELECT api.orders_dv__check()->>'status';") == "OK")
        # Grants are a deployment manifest, reapplied before the acceptance/commit point.
        script = script.replace("COMMIT;", f"""
GRANT SELECT ON api.orders_dv TO {role};
COMMIT;""")
        commands.sql(db, script)
        record(RESULT_IDS[1], commands.sql(db, "SELECT data->>'status' FROM api.orders_dv WHERE _id=42;") == "new"
               and commands.sql(db, "SELECT api.orders_dv__check()->>'status';") == "OK")
        record(RESULT_IDS[3], commands.sql(db, f"SET ROLE {role}; SELECT count(*) FROM api.orders_dv;") == "SET\n1"
               and commands.sql(db, "SELECT api.orders_dv__check(7);") == "7"
               and commands.sql(db, "SELECT count(*) FROM pg_policy WHERE polrelid='shop.orders'::regclass;") == "1")
    finally:
        commands.drop(db)
        commands.sql(args.maintenance_database, f"DROP ROLE IF EXISTS {role};")
        if args.output:
            args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in results))
    print("upgrade documentation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
