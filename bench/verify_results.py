#!/usr/bin/env python3
"""Validate benchmark artifacts and recompute every derived statistic."""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
from pathlib import Path
import statistics


IMPLEMENTATIONS = ("direct", "duality")
STATISTICS = ("median_ms", "p95_ms", "min_ms", "max_ms")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_results(folder: Path) -> dict[str, int | str]:
    folder = folder.resolve()
    environment = json.loads((folder / "environment.json").read_text())
    summary_rows = json.loads((folder / "summary.json").read_text())
    plans = json.loads((folder / "plans.json").read_text())
    with (folder / "measurements.csv").open(newline="") as source:
        measurements = list(csv.DictReader(source))

    sizes = tuple(int(value) for value in environment["sizes"])
    operations = tuple(environment.get("operations") or
                       dict.fromkeys(row["operation"] for row in measurements))
    warmups = int(environment["warmups"])
    repetitions = int(environment["repetitions"])
    total_runs = warmups + repetitions
    expected_groups = len(sizes) * len(operations)
    expected_measurements = expected_groups * len(IMPLEMENTATIONS) * total_runs
    require(warmups >= 0 and repetitions > 0, "invalid sampling configuration")
    require(bool(sizes) and len(set(sizes)) == len(sizes)
            and all(size >= 0 for size in sizes),
            "invalid size configuration")
    require(len(set(operations)) == len(operations) and operations,
            "invalid operation configuration")
    require(len(measurements) == expected_measurements,
            f"expected {expected_measurements} measurements, found {len(measurements)}")
    require(len(summary_rows) == expected_groups,
            f"expected {expected_groups} summary groups, found {len(summary_rows)}")
    require(all(row["status"] == "PASS" for row in measurements),
            "measurements contain a non-PASS result")

    grouped: dict[tuple[int, str, str], list[dict[str, str]]] = collections.defaultdict(list)
    for row in measurements:
        key = int(row["size"]), row["operation"], row["implementation"]
        require(key[0] in sizes and key[1] in operations and key[2] in IMPLEMENTATIONS,
                f"unexpected measurement group {key}")
        grouped[key].append(row)
    require(len(grouped) == expected_groups * len(IMPLEMENTATIONS),
            "measurement groups are incomplete")

    for key, samples in grouped.items():
        require(len(samples) == total_runs, f"{key} has an invalid sample count")
        require([int(row["run"]) for row in samples] == list(range(total_runs)),
                f"{key} has an invalid run sequence")
        require(all(row["warmup"] in {"True", "False"} for row in samples),
                f"{key} contains a noncanonical warm-up flag")
        require(all((row["warmup"] == "True") == (int(row["run"]) < warmups)
                    for row in samples), f"{key} has an invalid warm-up structure")
        require(sum(row["warmup"] == "False" for row in samples) == repetitions,
                f"{key} has an invalid measured-sample count")
        for row in samples:
            for field in ("execution_ms", "planning_ms", "shared_hit_blocks",
                          "shared_dirtied_blocks", "wal_records"):
                value = float(row[field])
                require(math.isfinite(value) and value >= 0,
                        f"{key} contains invalid {field}")

    summaries = {(int(row["size"]), row["operation"]): row for row in summary_rows}
    require(set(summaries) == {(size, operation) for size in sizes for operation in operations},
            "summary groups do not match the configured workload")
    expected_plan_keys = {
        f"{size}:{operation}:{implementation}"
        for size in sizes for operation in operations for implementation in IMPLEMENTATIONS
    }
    require(set(plans) == expected_plan_keys, "captured plan groups are incomplete or unexpected")

    for (size, operation), summary in summaries.items():
        calculated: dict[str, dict[str, float]] = {}
        for implementation in IMPLEMENTATIONS:
            key = size, operation, implementation
            samples = grouped[key]
            values = sorted(float(row["execution_ms"]) for row in samples
                            if row["warmup"] == "False")
            calculated[implementation] = {
                "median_ms": statistics.median(values),
                "p95_ms": values[int(.95 * (len(values) - 1))],
                "min_ms": min(values),
                "max_ms": max(values),
            }
            for field in STATISTICS:
                require(math.isclose(calculated[implementation][field],
                                     float(summary[implementation][field]),
                                     rel_tol=1e-12, abs_tol=1e-12),
                        f"{key} {field} differs from raw measurements")
            first_measured = next(row for row in samples if int(row["run"]) == warmups)
            plan_time = sum(float(plan["Execution Time"])
                            for plan in plans[f"{size}:{operation}:{implementation}"])
            require(math.isclose(plan_time, float(first_measured["execution_ms"]),
                                 rel_tol=1e-12, abs_tol=1e-12),
                    f"{key} captured plan differs from its measurement")
        require(calculated["direct"]["median_ms"] > 0,
                f"{(size, operation)} direct median must be positive")
        ratio = calculated["duality"]["median_ms"] / calculated["direct"]["median_ms"]
        require(math.isclose(ratio, float(summary["median_ratio"]),
                             rel_tol=1e-12, abs_tol=1e-12),
                f"{(size, operation)} median ratio differs from raw measurements")

    return {"status": "PASS", "measurements": len(measurements),
            "groups": len(summary_rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_results(args.folder), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
