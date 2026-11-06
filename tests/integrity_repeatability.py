#!/usr/bin/env python3
"""Run the complete integrity suite twice in one existing database."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


TEST_NAME = "PGJT-41 complete integrity suite is repeatable in one database"
EXPECTED_RESULT_COUNT = 39
EXPECTED_RESULT_ID_DIGEST = (
    "9e34cf635b3253b68a31bb8e4192a56c7d84eae6d012edf133cb1afa1481438d")


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError(f"duplicate JSON key in integrity result: {key}")
        result[key] = value
    return result


def load_results(path: Path) -> list[dict]:
    rows = [json.loads(line, object_pairs_hook=reject_duplicate_keys)
            for line in path.read_text().splitlines()
            if line.strip()]
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{path}: integrity run has no result objects")
    names = [row.get("name") for row in rows]
    if any(not isinstance(name, str) or not name for name in names):
        raise RuntimeError(f"{path}: integrity run has an invalid result name")
    if len(names) != len(set(names)):
        raise RuntimeError(f"{path}: integrity run has duplicate result names")
    failed = [row for row in rows if row.get("status") != "PASS"]
    if failed:
        raise RuntimeError(f"{path}: integrity run has non-PASS results")
    return rows


def verify_runs(first_path: Path, second_path: Path, *,
                expected_count: int = EXPECTED_RESULT_COUNT,
                expected_digest: str = EXPECTED_RESULT_ID_DIGEST,
                ) -> tuple[int, list[str]]:
    first = load_results(first_path)
    second = load_results(second_path)
    first_names = [row["name"] for row in first]
    second_names = [row["name"] for row in second]
    if first_names != second_names:
        raise RuntimeError("integrity result IDs differ between repeated runs")
    digest = hashlib.sha256("\n".join(first_names).encode()).hexdigest()
    if len(first_names) != expected_count or digest != expected_digest:
        raise RuntimeError(
            "integrity result IDs differ from the reviewed PGJT-41 inventory")
    return len(first_names), first_names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeatability-output", type=Path, required=True)
    parser.add_argument(
        "--suite", type=Path,
        default=Path(__file__).with_name("integrity_regressions.py"))
    args = parser.parse_args()
    aliases = args.output.resolve() == args.repeatability_output.resolve()
    if (not aliases and args.output.exists()
            and args.repeatability_output.exists()):
        aliases = args.output.samefile(args.repeatability_output)
    if aliases:
        raise RuntimeError(
            "integrity and repeatability outputs must use distinct paths")

    with tempfile.TemporaryDirectory(prefix="pgjt-41-repeatability-") as temporary:
        scratch = Path(temporary)
        first = scratch / "first.jsonl"
        second = scratch / "second.jsonl"
        command = [sys.executable, str(args.suite), "--psql", args.psql,
                   "--database", args.database]
        for run, output in ((1, first), (2, second)):
            completed = subprocess.run(command + ["--output", str(output)])
            if completed.returncode:
                raise RuntimeError(
                    f"integrity suite run {run} failed with status "
                    f"{completed.returncode}")
        count, names = verify_runs(first, second)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(second.read_text())

    result = {"name": TEST_NAME, "status": "PASS", "observed": {
        "runs": 2, "database_recreated": False, "result_count": count,
        "result_ids": names}}
    args.repeatability_output.parent.mkdir(parents=True, exist_ok=True)
    args.repeatability_output.write_text(json.dumps(result, sort_keys=True) + "\n")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
