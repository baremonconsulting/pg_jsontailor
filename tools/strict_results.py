"""Shared fail-closed JSON decoding and reviewed result inventories."""
from __future__ import annotations

import json
from pathlib import Path

INVENTORY = Path(__file__).resolve().parents[1] / "tests/suite_inventory.json"


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise RuntimeError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(content: str):
    def invalid(value):
        raise RuntimeError(f"non-finite JSON constant: {value}")
    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)


def result_rows(path: Path) -> list[dict]:
    content = path.read_text()
    try:
        value = loads(content)
    except json.JSONDecodeError:
        values = [loads(line) for line in content.splitlines() if line.strip()]
    else:
        values = value if isinstance(value, list) else [value]
    if not values or not all(isinstance(row, dict) for row in values):
        raise RuntimeError(f"{path}: no test results or non-object rows")
    return values


def identifier(row):
    candidates = [row[key] for key in ("name", "id", "test_id") if key in row]
    if len(candidates) != 1 or not isinstance(candidates[0], str) or not candidates[0]:
        raise RuntimeError("result must have exactly one name or id")
    return candidates[0]


def inventories():
    return loads(INVENTORY.read_text())["suites"]


def validate(rows: list[dict], suite: str | None = None, *, allow_failures=False):
    ids = [identifier(row) for row in rows]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate test identifiers")
    if not allow_failures and any(row.get("status") != "PASS" for row in rows):
        raise RuntimeError("non-PASS runtime result did not pass")
    if allow_failures and any(row.get("status") not in {"PASS", "FAIL"} for row in rows):
        raise RuntimeError("invalid result status")
    if suite is not None:
        expected = inventories().get(suite)
        if expected is None:
            raise RuntimeError(f"unknown reviewed suite: {suite}")
        if len(expected) != len(set(expected)) or set(ids) != set(expected):
            raise RuntimeError(f"{suite}: inventory differs: missing={sorted(set(expected)-set(ids))}, "
                               f"extra={sorted(set(ids)-set(expected))}")
    return rows
