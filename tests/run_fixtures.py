#!/usr/bin/env python3
"""Run the JSONL contract corpus against an installed pg_jsontailor."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess
import sys


def reject_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def parse_json(value: str):
    return json.loads(value, parse_float=Decimal, parse_int=Decimal,
                      parse_constant=reject_constant)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_input(spec: dict) -> str:
    if spec["kind"] == "sql_null":
        return "NULL::jsonb"
    if spec["kind"] == "json":
        return f"{sql_literal(spec['text'])}::jsonb"
    raise ValueError(f"unsupported input kind: {spec['kind']}")


def query_for(case: dict, function: str) -> str:
    target = sql_input(case["target"])
    patch = sql_input(case["patch"])
    expected = case["expected"]
    if expected["kind"] == "json":
        expected_expr = f"{sql_literal(expected['text'])}::jsonb"
        comparison = (
            "CASE WHEN actual IS NULL THEN false "
            f"ELSE actual OPERATOR(pg_catalog.=) {expected_expr} END"
        )
    elif expected["kind"] == "sql_null":
        comparison = "actual IS NULL"
    else:
        comparison = "false"
    return f"""
\\set VERBOSITY verbose
WITH evaluated AS MATERIALIZED (
    SELECT {function}({target}, {patch}) AS actual
)
SELECT CASE WHEN actual IS NULL THEN 'sql_null' ELSE 'json' END,
       CASE WHEN actual IS NULL THEN '' ELSE actual::text END,
       ({comparison})::text
FROM evaluated;
"""


def sqlstate(stderr: str) -> str | None:
    match = re.search(r"ERROR:\s+([0-9A-Z]{5}):", stderr)
    return match.group(1) if match else None


def run_case(psql: str, database: str, case: dict, function: str,
             implementation: str) -> dict:
    command = [psql, "-X", "-A", "-t", "-F", "\t", "-v", "ON_ERROR_STOP=1",
               "-d", database]
    completed = subprocess.run(command, input=query_for(case, function), text=True,
                               capture_output=True, check=False)
    expected = case["expected"]
    result = {
        "id": case["id"],
        "profile": case["profile"],
        "implementation": implementation,
        "expected_kind": expected["kind"],
    }
    if completed.returncode:
        result.update({
            "actual_kind": "error",
            "sqlstate": sqlstate(completed.stderr),
            "message": completed.stderr.strip(),
            "phase": "input_parsing" if expected["kind"] == "error" else "call",
            "category": "odmítnuto",
            "passed": expected["kind"] == "error",
        })
        return result

    fields = completed.stdout.rstrip("\n").split("\t")
    if len(fields) != 3:
        result.update({"actual_kind": "runner_error", "category": "FAIL",
                       "passed": False, "message": completed.stdout + completed.stderr})
        return result
    actual_kind, actual_text, server_equal = fields
    result.update({"actual_kind": actual_kind, "actual_text": actual_text})
    passed = server_equal == "true" and expected["kind"] != "error"
    result["passed"] = passed
    if passed:
        result["category"] = "zachováno"
    elif actual_kind == "json" and expected["kind"] == "json":
        # Decimal parsing independently guards the export path against float loss.
        try:
            parse_json(actual_text)
            parse_json(expected["text"])
        except (ValueError, json.JSONDecodeError) as exc:
            result["export_error"] = str(exc)
        result["category"] = "tichá změna hodnoty"
    else:
        result["category"] = "FAIL"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    parser.add_argument("--fixtures", type=Path,
                        default=Path(__file__).with_name("fixtures.jsonl"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--function", default="jsontailor.merge_patch")
    parser.add_argument("--implementation", default="pg_jsontailor")
    parser.add_argument("--allow-differences", action="store_true")
    args = parser.parse_args()

    cases = [json.loads(line) for line in args.fixtures.read_text().splitlines()
             if line.strip()]
    if len({case["id"] for case in cases}) != len(cases):
        raise SystemExit("fixture IDs are not unique")
    if sum(case["id"].startswith("rfc_a") for case in cases) != 15:
        raise SystemExit("the corpus does not contain exactly 15 RFC cases")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with args.output.open("w", encoding="utf-8") as stream:
        for case in cases:
            result = run_case(args.psql, args.database, case, args.function,
                              args.implementation)
            stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            failures += not result["passed"]
            print(f"{case['id']}: {'PASS' if result['passed'] else 'FAIL'}")
    print(f"cases={len(cases)} failures={failures}")
    return 1 if failures and not args.allow_differences else 0


if __name__ == "__main__":
    sys.exit(main())
