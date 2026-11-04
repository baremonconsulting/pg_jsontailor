#!/usr/bin/env python3
"""Focused correctness regression for set-based array reconciliation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def run(sql: str) -> str:
        completed = subprocess.run(
            [args.psql, "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", args.database],
            input=sql, text=True, capture_output=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        return completed.stdout.strip()

    run(args.schema.read_text())
    run("SELECT jsontailor.create_duality_view('shop.orders_dv', " +
        literal(args.spec.read_text()) + "::jsonb)")

    doc = json.loads(run("SELECT data::text FROM shop.orders_dv WHERE _id=42"))
    doc["items"] = [{"product": f"bulk-{n}", "qty": n, "price": n / 10}
                    for n in range(1, 101)]
    run("UPDATE shop.orders_dv SET data=" + literal(json.dumps(doc)) + "::jsonb WHERE _id=42")
    added = run("SELECT count(*)||':'||sum(quantity) FROM shop.order_items WHERE order_id=42")

    doc = json.loads(run("SELECT data::text FROM shop.orders_dv WHERE _id=42"))
    for item in doc["items"]:
        item["qty"] += 1
    run("UPDATE shop.orders_dv SET data=" + literal(json.dumps(doc)) + "::jsonb WHERE _id=42")
    updated = run("SELECT count(*)||':'||sum(quantity) FROM shop.order_items WHERE order_id=42")

    doc = json.loads(run("SELECT data::text FROM shop.orders_dv WHERE _id=42"))
    doc["items"] = doc["items"][:50]
    run("UPDATE shop.orders_dv SET data=" + literal(json.dumps(doc)) + "::jsonb WHERE _id=42")
    deleted = run("SELECT count(*)||':'||sum(quantity) FROM shop.order_items WHERE order_id=42")

    insert_doc = {
        "_id": 300, "status": "bulk", "created": "2026-11-03",
        "customer": {"customerId": 7},
        "items": [{"itemId": 300000 + n, "product": f"insert-{n}",
                   "qty": n, "price": n / 10} for n in range(1, 101)]}
    run("INSERT INTO shop.orders_dv(data) VALUES(" +
        literal(json.dumps(insert_doc)) + "::jsonb)")
    inserted = run("SELECT count(*)||':'||sum(quantity) FROM shop.order_items WHERE order_id=300")

    observed = {"add": added, "update": updated, "delete": deleted, "insert": inserted}
    expected = {"add": "100:5050", "update": "100:5150",
                "delete": "50:1325", "insert": "100:5050"}
    result = {"status": "PASS" if observed == expected else "FAIL",
              "expected": expected, "observed": observed}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
