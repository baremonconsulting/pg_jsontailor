#!/usr/bin/env python3
"""Run the shared corpus against MySQL JSON_MERGE_PATCH."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess


def reject_constant(value: str) -> None:
    raise ValueError(value)


def parse(value: str):
    return json.loads(value, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=reject_constant)


def equivalent(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Decimal) or isinstance(right, Decimal):
        return isinstance(left, Decimal) and isinstance(right, Decimal) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(equivalent(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
    return left == right


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"


def expression(spec: dict) -> str:
    if spec["kind"] == "sql_null":
        return "NULL"
    return f"CAST({literal(spec['text'])} AS JSON)"


def run(mysql: str, socket: str, case: dict) -> dict:
    query = ("SELECT JSON_MERGE_PATCH("
             f"{expression(case['target'])},{expression(case['patch'])});")
    command = [mysql, "--no-defaults", f"--socket={socket}", "-uroot",
               "--batch", "--raw", "--skip-column-names"]
    completed = subprocess.run(command, input=query, text=True,
                               capture_output=True, check=False)
    expected = case["expected"]
    result = {"id": case["id"], "profile": case["profile"],
              "implementation": "mysql_8_4_13",
              "expected_kind": expected["kind"]}
    if completed.returncode:
        match = re.search(r"ERROR\s+(\d+)\s+\(([0-9A-Z]+)\)", completed.stderr)
        result.update({"actual_kind": "error",
                       "error_code": int(match.group(1)) if match else None,
                       "sqlstate": match.group(2) if match else None,
                       "message": completed.stderr.strip(), "phase": "input_parsing"})
        result["passed"] = expected["kind"] == "error"
        result["category"] = "odmítnuto"
        return result
    actual = completed.stdout.rstrip("\n")
    if actual == "NULL":
        result.update({"actual_kind": "sql_null", "actual_text": None})
        result["passed"] = expected["kind"] == "sql_null"
    else:
        result.update({"actual_kind": "json", "actual_text": actual})
        try:
            result["passed"] = (expected["kind"] == "json" and
                                equivalent(parse(actual), parse(expected["text"])))
        except (ValueError, json.JSONDecodeError) as exc:
            result["passed"] = False
            result["comparison_error"] = str(exc)
    if result["passed"]:
        result["category"] = "zachováno"
    elif result["actual_kind"] == "json" and expected["kind"] == "json":
        result["category"] = "tichá změna hodnoty"
    else:
        result["category"] = "očekávaná odchylka"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mysql", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).with_name("fixtures.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [json.loads(line) for line in args.fixtures.read_text().splitlines() if line]
    results = [run(args.mysql, args.socket, case) for case in cases]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                   for row in results))
    for row in results:
        print(f"{row['id']}: {'MATCH' if row['passed'] else row['category']}")
    print(f"cases={len(results)} matches={sum(r['passed'] for r in results)} "
          f"differences={sum(not r['passed'] for r in results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
