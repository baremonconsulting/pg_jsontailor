#!/usr/bin/env python3
"""Verify public version metadata and reference benchmark claims."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "bench/reference/azure-11e6d74/summary.json"
INTERMEDIATE = ROOT / "bench/reference/azure-afa643c/summary.json"
HISTORICAL = ROOT / "bench/reference/azure-04d053e/summary.json"


def text(path: str) -> str:
    return (ROOT / path).read_text()


def rows(path: Path) -> dict[tuple[int, str], dict]:
    return {(row["size"], row["operation"]): row
            for row in json.loads(path.read_text())}


def benchmark_verifier():
    path = ROOT / "bench/verify_results.py"
    spec = importlib.util.spec_from_file_location("benchmark_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("benchmark verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify_results


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    control = text("pg_jsontailor.control")
    version = re.search(r"^default_version = '([^']+)'$", control, re.MULTILINE)
    require(version is not None, "default_version is missing")
    value = version.group(1)
    require(f"version: {value}" in text("CITATION.cff"),
            "CITATION.cff version differs from the control file")
    require(f"## {value} —" in text("CHANGELOG.md"),
            "CHANGELOG version differs from the control file")
    require((ROOT / f"sql/pg_jsontailor--{value}.sql").is_file(),
            "versioned extension SQL is missing")
    require(f"DATA = sql/pg_jsontailor--{value}.sql" in text("Makefile"),
            "Makefile version differs from the control file")

    verify_benchmark = benchmark_verifier()
    verify_benchmark(CURRENT.parent)
    verify_benchmark(INTERMEDIATE.parent)
    verify_benchmark(HISTORICAL.parent)
    current = rows(CURRENT)
    intermediate = rows(INTERMEDIATE)

    claims = {
        (1000, "read"): (4.270, 4.311, 2.883),
        (1000, "insert"): (32.208, 32.520, 6.277),
        (1000, "root_update"): (34.642, 34.951, 0.048),
        (1000, "update"): (50.471, 50.876, 6.066),
        (1000, "delete"): (13.256, 13.961, 2.317),
        (1000, "merge_insert"): (32.229, 32.560, 7.139),
        (1000, "merge_update"): (49.282, 49.664, 6.054),
        (1000, "merge_delete"): (17.798, 19.929, 3.154),
    }
    for key, expected in claims.items():
        row = current[key]
        observed = (row["duality"]["median_ms"],
                    row["duality"]["p95_ms"],
                    row["direct"]["median_ms"])
        require(all(abs(actual - published) <= 0.00051
                    for actual, published in zip(observed, expected)),
                f"reference benchmark claim differs for {key}: {observed}")

    expected_improvements = {
        "insert": 69.0,
        "root_update": 67.7,
        "update": 58.9,
        "merge_insert": 69.1,
        "merge_update": 59.3,
    }
    for operation, expected in expected_improvements.items():
        before = intermediate[1000, operation]["duality"]["median_ms"]
        after = current[1000, operation]["duality"]["median_ms"]
        observed = round((1 - after / before) * 100, 1)
        require(observed == expected,
                f"set-wise validation comparison changed for {operation}: {observed}")

    benchmarks = text("docs/BENCHMARKS.md")
    readme = text("README.md")
    for claim in (
            "50.471 ms | 50.876 ms | 6.066 ms",
            "32.229 ms | 32.560 ms | 7.139 ms",
            "49.282 ms | 49.664 ms | 6.054 ms",
            "17.798 ms | 19.929 ms | 3.154 ms",
            "69.0%, 67.7%, 58.9%, 69.1% and 59.3%"):
        require(claim in benchmarks, f"published benchmark claim is missing: {claim}")
    require("50.471 ms" in readme and "50.876 ms" in readme,
            "README benchmark summary differs from the current reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
