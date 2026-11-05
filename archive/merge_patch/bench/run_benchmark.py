#!/usr/bin/env python3
"""Run alternating server-side benchmarks and emit raw CSV measurements."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import subprocess


IMPLEMENTATIONS = {
    "pg_jsontailor": "jsontailor.merge_patch",
    "jsonvd": "jsonvd.merge_patch",
}
PROFILES = ("portable_1k", "portable_16k", "stress_256k")


def explain(psql: str, database: str, function: str, profile: str) -> tuple[float, str]:
    query = f"""
SET jit = off;
SET max_parallel_workers_per_gather = 0;
EXPLAIN (ANALYZE, TIMING OFF, VERBOSE, FORMAT JSON)
SELECT count(*),
       sum(pg_catalog.pg_column_size(
           {function}(b.target, b.patch)))
FROM bench_inputs AS b
WHERE b.profile = '{profile}';
"""
    completed = subprocess.run(
        [psql, "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=query, text=True, capture_output=True, check=True)
    payload = json.loads(completed.stdout)[0]
    plan_text = json.dumps(payload["Plan"], sort_keys=True)
    if plan_text.count(function) != 1:
        raise RuntimeError(f"{function} occurs {plan_text.count(function)} times in plan")
    return float(payload["Execution Time"]), plan_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plans", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.plans.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    saved_plans = {}
    for profile in PROFILES:
        for repetition in range(6):
            names = list(IMPLEMENTATIONS)
            if repetition % 2:
                names.reverse()
            for name in names:
                elapsed, plan = explain(args.psql, args.database,
                                        IMPLEMENTATIONS[name], profile)
                rows.append({
                    "profile": profile,
                    "implementation": name,
                    "run": repetition,
                    "warmup": repetition == 0,
                    "execution_ms": elapsed,
                    "status": "PASS",
                })
                saved_plans[f"{profile}:{name}"] = json.loads(plan)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    args.plans.write_text(json.dumps(saved_plans, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
