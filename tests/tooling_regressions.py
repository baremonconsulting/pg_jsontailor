#!/usr/bin/env python3
"""Regression checks for benchmark evidence and pilot acceptance tooling."""
from __future__ import annotations

import ast
import csv
import importlib.util
import json
from pathlib import Path
import shutil
import statistics
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def load_verifier():
    path = ROOT / "bench/verify_results.py"
    spec = importlib.util.spec_from_file_location("benchmark_verifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_results


def expect_rejection(verify, folder: Path, expected: str) -> None:
    try:
        verify(folder)
    except RuntimeError as error:
        if expected not in str(error):
            raise AssertionError(f"unexpected rejection: {error}") from error
    else:
        raise AssertionError(f"malformed benchmark was accepted: {folder}")


def benchmark_checks() -> None:
    verify = load_verifier()
    source = ROOT / "bench/reference/azure-04d053e"
    with tempfile.TemporaryDirectory(prefix="pgjt-tooling-") as temporary:
        scratch = Path(temporary)
        changed = scratch / "changed"
        shutil.copytree(source, changed)
        with (changed / "measurements.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            fields = reader.fieldnames
            rows = list(reader)
        row = next(row for row in rows if row["warmup"] == "False")
        row["warmup"] = "false"
        with (changed / "measurements.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        expect_rejection(verify, changed, "noncanonical warm-up flag")

        empty = scratch / "empty"
        empty.mkdir()
        (empty / "environment.json").write_text(json.dumps({
            "sizes": [], "operations": ["read"], "warmups": 3, "repetitions": 30}))
        (empty / "summary.json").write_text("[]")
        (empty / "plans.json").write_text("{}")
        with (empty / "measurements.csv").open("w", newline="") as stream:
            csv.DictWriter(stream, fieldnames=fields).writeheader()
        expect_rejection(verify, empty, "invalid size configuration")


def pilot_check() -> None:
    path = ROOT / "pilot/synthetic/run_pilot.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    selected = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for index, statement in enumerate(node.body):
            if (isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                    and statement.value.func.id == "add_check"
                    and isinstance(statement.value.args[0], ast.Constant)
                    and statement.value.args[0].value == "operational-read-sample"):
                selected = node.body[index - 2:index + 1]
    if selected is None:
        raise AssertionError("operational-read acceptance block is missing")

    checks = []

    def http(*_args, **_kwargs):
        return 500, {"error": "injected"}, 2.0

    def add_check(name, passed, observed, expected):
        checks.append((name, passed, observed, expected))

    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(path), "exec"), {
        "http": http, "add_check": add_check, "statistics": statistics})
    if len(checks) != 1 or checks[0][0] != "operational-read-sample" or checks[0][1]:
        raise AssertionError(f"HTTP errors passed the pilot acceptance check: {checks}")


def main() -> int:
    benchmark_checks()
    pilot_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
