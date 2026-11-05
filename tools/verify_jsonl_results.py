#!/usr/bin/env python3
"""Validate JSON Lines test output without duplicating suite-specific counts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify(path: Path) -> int:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise RuntimeError(f"{path} contains no test results")
    names = [row.get("name") for row in rows]
    if any(not isinstance(name, str) or not name for name in names):
        raise RuntimeError(f"{path} contains a result without a name")
    if len(names) != len(set(names)):
        raise RuntimeError(f"{path} contains duplicate test names")
    failures = [row for row in rows if row.get("status") != "PASS"]
    if failures:
        raise RuntimeError(f"{path} contains {len(failures)} non-PASS results")
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        print(f"{path}: {verify(path)} PASS results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
