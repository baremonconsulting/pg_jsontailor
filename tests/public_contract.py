#!/usr/bin/env python3
"""Runtime witnesses for the precise public ETag and deletion contract."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import uuid

from dump_restore import Commands, literal
from standalone_generated_sql import failed_sql, last

ROOT = Path(__file__).resolve().parents[1]
RESULT_IDS = ("PGJT-67/etag-coverage", "PGJT-67/direct-delete-predicate",
              "PGJT-67/mapping-restrictions")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commands = Commands(args.pg_bin, args.maintenance_database)
    database = "pgjt67_" + uuid.uuid4().hex[:12]
    rows = []

    def record(name: str, passed: bool) -> None:
        rows.append({"name": name, "status": "PASS" if passed else "FAIL"})
        if not passed:
            raise RuntimeError(name + " failed")

    commands.create(database)
    try:
        commands.sql(database, "CREATE EXTENSION pg_jsontailor;")
        commands.run("psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", database,
                     "-f", str(ROOT / "examples/shop_schema.sql"))
        spec = json.loads((ROOT / "examples/orders_dv.spec.json").read_text())
        commands.sql(database, "CREATE SCHEMA api;SELECT jsontailor.create_duality_view("
                     "'api.orders_dv'," + literal(json.dumps(spec)) + "::jsonb);")
        before = last(commands, database, "SELECT data#>>'{_metadata,etag}' FROM api.orders_dv WHERE _id=42;")
        expected = last(commands, database, """SELECT upper(encode(substr(sha256(convert_to(
 jsonb_set(data-'_metadata','{customer}',jsonb_build_object('customerId',
 data#>'{customer,customerId}'))::text,'UTF8')),1,16),'hex'))
 FROM api.orders_dv WHERE _id=42;""")
        commands.sql(database, "UPDATE shop.customers SET name='renamed' WHERE customer_id=7;")
        renamed = last(commands, database, "SELECT data#>>'{_metadata,etag}' FROM api.orders_dv WHERE _id=42;")
        commands.sql(database, "UPDATE shop.orders SET customer_id=8 WHERE order_id=42;")
        changed_key = last(commands, database, "SELECT data#>>'{_metadata,etag}' FROM api.orders_dv WHERE _id=42;")
        record(RESULT_IDS[0], before == expected == renamed and changed_key != before
               and len(before) == 32 and before == before.upper())
        stale = last(commands, database, f"""WITH removed AS (DELETE FROM api.orders_dv
 WHERE _id=42 AND data#>>'{{_metadata,etag}}'={literal(before)} RETURNING _id)
 SELECT count(*) FROM removed;""")
        # No predicate can be mandated by a row trigger. Roll back this demonstration.
        unguarded = commands.sql(database, "BEGIN;DELETE FROM api.orders_dv WHERE _id=42 RETURNING _id;ROLLBACK;")
        record(RESULT_IDS[1], stale == "0" and "DELETE 1" in unguarded
               and last(commands, database, "SELECT count(*) FROM shop.orders WHERE order_id=42;") == "1")
        commands.sql(database, "ALTER TABLE shop.orders ADD COLUMN computed text GENERATED ALWAYS AS (status||'!') STORED;")
        changes = [lambda s: s["root"]["fields"].update({"bad": "computed"}),
                   lambda s: s["root"]["fields"].update({"bad": "order_id"}),
                   lambda s: s["root"]["fields"].update({"bad": "customer_id"}),
                   lambda s: s["array"]["fields"].update({"bad": "order_id"}),
                   lambda s: s["object"]["fields"].pop("customerId"),
                   lambda s: s["array"]["fields"].pop("itemId")]
        for change in changes:
            candidate = copy.deepcopy(spec)
            change(candidate)
            failed_sql(commands, database, "SELECT jsontailor.duality_view_ddl('api.rejected',"
                       + literal(json.dumps(candidate)) + "::jsonb);", "22023")
        record(RESULT_IDS[2], True)
    finally:
        commands.drop(database)
        args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    print("public contract runtime witnesses: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
