#!/usr/bin/env python3
"""Verify public version metadata and reference benchmark claims."""
from __future__ import annotations

import json
from pathlib import Path
import re
import csv


ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "bench/reference/azure-04d053e/summary.json"
BASELINE = ROOT / "bench/reference/azure-acecaae/summary.json"


def text(path: str) -> str:
    return (ROOT / path).read_text()


def rows(path: Path) -> dict[tuple[int, str], dict]:
    return {(row["size"], row["operation"]): row
            for row in json.loads(path.read_text())}


def measurements(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as source:
        return list(csv.DictReader(source))


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

    current = rows(CURRENT)
    baseline = rows(BASELINE)
    require(len(current) == 40, "reference benchmark must contain 40 groups")
    require(len(baseline) == 25, "baseline benchmark must contain 25 groups")
    current_measurements = measurements(CURRENT.with_name("measurements.csv"))
    baseline_measurements = measurements(BASELINE.with_name("measurements.csv"))
    require(len(current_measurements) == 2640,
            "reference benchmark must contain 2,640 executions")
    require(len(baseline_measurements) == 1650,
            "baseline benchmark must contain 1,650 executions")
    require(all(row["status"] == "PASS" for row in current_measurements),
            "reference benchmark contains a failed execution")
    require(all(row["status"] == "PASS" for row in baseline_measurements),
            "baseline benchmark contains a failed execution")

    claims = {
        (1000, "read"): (4.205, 4.224, 2.874),
        (1000, "insert"): (26.433, 26.769, 9.419),
        (1000, "root_update"): (25.180, 25.314, 0.054),
        (1000, "update"): (39.613, 39.773, 5.866),
        (1000, "delete"): (11.323, 11.475, 2.065),
        (1000, "merge_insert"): (22.712, 22.979, 6.515),
        (1000, "merge_update"): (41.855, 42.133, 6.104),
        (1000, "merge_delete"): (17.215, 17.367, 2.778),
    }
    for key, expected in claims.items():
        row = current[key]
        observed = (row["duality"]["median_ms"],
                    row["duality"]["p95_ms"],
                    row["direct"]["median_ms"])
        require(all(abs(actual - published) <= 0.00051
                    for actual, published in zip(observed, expected)),
                f"reference benchmark claim differs for {key}: {observed}")

    improvements = []
    insert_increases = []
    for size in (0, 1, 10, 100, 1000):
        old_update = baseline[size, "update"]["duality"]["median_ms"]
        new_update = current[size, "update"]["duality"]["median_ms"]
        improvements.append(round((1 - new_update / old_update) * 100, 1))
        old_insert = baseline[size, "insert"]["duality"]["median_ms"]
        new_insert = current[size, "insert"]["duality"]["median_ms"]
        insert_increases.append(round((new_insert / old_insert - 1) * 100, 1))
    require(improvements == [7.9, 32.1, 26.6, 14.9, 6.9],
            f"update comparison changed: {improvements}")
    require(min(insert_increases) == 5.5 and max(insert_increases) == 56.7,
            f"insert comparison changed: {insert_increases}")

    benchmarks = text("docs/BENCHMARKS.md")
    readme = text("README.md")
    for claim in (
            "39.613 ms | 39.773 ms | 5.866 ms",
            "22.712 ms | 22.979 ms | 6.515 ms",
            "41.855 ms | 42.133 ms | 6.104 ms",
            "17.215 ms | 17.367 ms | 2.778 ms",
            "7.9% at zero children, 32.1% at one, 26.6% at ten, "
            "14.9% at 100 and 6.9% at 1,000",
            "Insert medians increased by 5.5–56.7%"):
        require(claim in benchmarks, f"published benchmark claim is missing: {claim}")
    require("39.613 ms median and 39.773 ms p95" in readme,
            "README benchmark summary differs from the reference data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
