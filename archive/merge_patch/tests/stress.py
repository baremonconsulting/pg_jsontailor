#!/usr/bin/env python3
"""Probe depth and payload limits with a finite statement timeout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def nested(depth: int, leaf: int) -> str:
    value: object = leaf
    for _ in range(depth):
        value = {"a": value}
    return json.dumps(value, separators=(",", ":"))


def run(psql: str, database: str, target: str, patch: str, timeout_ms: int):
    sql = (
        "\\set VERBOSITY verbose\n"
        f"SET statement_timeout={timeout_ms};"
        "SELECT pg_catalog.pg_column_size(jsontailor.merge_patch("
        f"{literal(target)}::jsonb,{literal(patch)}::jsonb));"
    )
    started = time.monotonic()
    result = subprocess.run([psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database],
                            input=sql, text=True, capture_output=True)
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    state = re.search(r"ERROR:\s+([0-9A-Z]{5}):", result.stderr)
    return {"returncode": result.returncode, "elapsed_ms": elapsed_ms,
            "sqlstate": state.group(1) if state else None,
            "message": result.stderr.strip(), "output": result.stdout.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=3000)
    args = parser.parse_args()
    rows = []
    for depth in (1, 8, 16, 32, 64, 96, 128, 192, 256, 384, 512):
        target, patch = nested(depth, 1), nested(depth, 2)
        row = {"kind": "depth", "depth": depth,
               "target_bytes": len(target.encode()), "patch_bytes": len(patch.encode())}
        row.update(run(args.psql, args.database, target, patch, args.timeout_ms))
        rows.append(row)
        if row["returncode"]:
            break
    for payload_bytes in (1024, 16384, 262144, 1048576):
        target = json.dumps({"payload": "x" * payload_bytes}, separators=(",", ":"))
        patch = '{"marker":1}'
        row = {"kind": "size", "requested_payload_bytes": payload_bytes,
               "target_bytes": len(target.encode()), "patch_bytes": len(patch)}
        row.update(run(args.psql, args.database, target, patch, args.timeout_ms))
        rows.append(row)
        if row["returncode"]:
            break
    recovery = run(args.psql, args.database, '{}', '{"ok":true}', args.timeout_ms)
    rows.append({"kind": "post_failure_recovery", **recovery})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    return 0 if recovery["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
