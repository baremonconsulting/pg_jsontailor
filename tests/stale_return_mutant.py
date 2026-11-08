#!/usr/bin/env python3
"""Require a targeted stale-RETURNING mutant to fail its exact witness.

This campaign drops/recreates the extension in a disposable database. It is
separate from the reviewed nightly five-property/six-mutation campaign.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from core_operations import Harness

NEEDLE = "EXECUTE format('SELECT data FROM %%s WHERE _id=$1',%L) USING root_id INTO NEW.data;"
REPLACEMENT = "EXECUTE format('SELECT data FROM %%s WHERE _id=$1',%L) USING root_id INTO cur;"
WITNESS = "RETURNING document differs from fresh read-back"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    harness = Harness(args.psql, args.database, args.repo / "examples/shop_schema.sql",
                      args.repo / "examples/orders_dv.spec.json")
    version = int(harness.scalar("SHOW server_version_num;")) // 10000
    if version not in {17, 18, 19}:
        raise RuntimeError("stale-return campaign requires PostgreSQL 17/18/19")
    source = "\n".join((args.repo / "sql/pg_jsontailor--0.2.0.sql").read_text().splitlines()[1:])
    if source.count(NEEDLE) != 1:
        raise RuntimeError("stale-return mutation must match exactly one generated UPDATE return")

    def load(value):
        harness.run("DROP EXTENSION IF EXISTS pg_jsontailor CASCADE;"
                    "DROP SCHEMA IF EXISTS jsontailor CASCADE;CREATE SCHEMA jsontailor;" + value)

    def control():
        harness.results.clear()
        harness.test1()
        if harness.results[-1]["status"] != "PASS":
            raise RuntimeError("stale-return positive control did not pass")

    killed = False
    load(source)
    control()
    try:
        load(source.replace(NEEDLE, REPLACEMENT))
        try:
            harness.test1()
        except RuntimeError as error:
            if str(error) != WITNESS:
                raise RuntimeError("mutant failed outside the expected RETURNING witness") from error
            killed = True
    finally:
        load(source)
        control()
    row = {"id": "PGJT-61/stale-return-mutant", "status": "PASS" if killed else "FAIL",
           "postgresql": str(version), "mutant_compiled": True, "mutant_killed": killed,
           "control_passed": True, "witness": "PGJT-63/core-1", "failure": WITNESS if killed else None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(row, sort_keys=True) + "\n")
    return 0 if killed else 1


if __name__ == "__main__":
    sys.exit(main())
