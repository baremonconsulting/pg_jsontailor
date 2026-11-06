#!/usr/bin/env python3
"""Verify public version metadata and reference benchmark claims."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "bench/reference/azure-e5b41a5-pg18/summary.json"
FINAL_SETWISE = ROOT / "bench/reference/azure-11e6d74/summary.json"
INTERMEDIATE = ROOT / "bench/reference/azure-afa643c/summary.json"
HISTORICAL = ROOT / "bench/reference/azure-04d053e/summary.json"
VERSION_REFERENCES = (
    ROOT / "bench/reference/azure-e5b41a5-pg17",
    ROOT / "bench/reference/azure-e5b41a5-pg18",
    ROOT / "bench/reference/azure-e5b41a5-pg19",
)
PREVIOUS_VERSION_REFERENCES = (
    ROOT / "bench/reference/azure-a9a455e-pg17",
    ROOT / "bench/reference/azure-a9a455e-pg18",
    ROOT / "bench/reference/azure-a9a455e-pg19",
)


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
    for reference in VERSION_REFERENCES:
        verify_benchmark(reference)
    for reference in PREVIOUS_VERSION_REFERENCES:
        verify_benchmark(reference)
    verify_benchmark(FINAL_SETWISE.parent)
    verify_benchmark(INTERMEDIATE.parent)
    verify_benchmark(HISTORICAL.parent)
    current = rows(CURRENT)
    final_setwise = rows(FINAL_SETWISE)
    intermediate = rows(INTERMEDIATE)

    claims = {
        (1000, "read"): (4.231, 4.293, 2.889),
        (1000, "insert"): (38.386, 38.951, 9.561),
        (1000, "root_update"): (35.038, 35.279, 0.058),
        (1000, "update"): (49.615, 50.436, 5.847),
        (1000, "delete"): (13.559, 13.902, 2.655),
        (1000, "merge_insert"): (34.207, 34.520, 6.521),
        (1000, "merge_update"): (51.791, 52.411, 6.008),
        (1000, "merge_delete"): (13.178, 13.464, 0.851),
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
        after = final_setwise[1000, operation]["duality"]["median_ms"]
        observed = round((1 - after / before) * 100, 1)
        require(observed == expected,
                f"set-wise validation comparison changed for {operation}: {observed}")

    benchmarks = text("docs/BENCHMARKS.md")
    readme = text("README.md")
    for claim in (
            "49.615 ms | 50.436 ms | 5.847 ms",
            "34.207 ms | 34.520 ms | 6.521 ms",
            "51.791 ms | 52.411 ms | 6.008 ms",
            "13.178 ms | 13.464 ms | 0.851 ms",
            "69.0%, 67.7%, 58.9%, 69.1% and 59.3%"):
        require(claim in benchmarks, f"published benchmark claim is missing: {claim}")
    require("49.615 ms" in readme and "50.436 ms" in readme,
            "README benchmark summary differs from the current reference")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
