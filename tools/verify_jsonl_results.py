#!/usr/bin/env python3
"""Validate JSON Lines test output without duplicating suite-specific counts."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import result_rows, validate

FILENAMES = {
    'helper-acl-results.jsonl': 'helper_acl',
    'runtime-limits-results.jsonl': 'runtime_limits',
    'http-security-results.jsonl': 'http_security',
    'http-concurrency-results.jsonl': 'http_concurrency',
    'core-results.jsonl': 'core', 'validation-results.jsonl': 'validation',
    'integrity-results.jsonl': 'integrity', 'repeatability-results.jsonl': 'repeatability',
    'subtransaction-results.jsonl': 'subtransaction', 'merge-results.jsonl': 'merge',
    'type-results.jsonl': 'types', 'shape-results.jsonl': 'shapes',
    'security-results.jsonl': 'security', 'partition-results.jsonl': 'partitions',
    'partition-restore-results.jsonl': 'partition_restore',
    'restore-matrix-results.jsonl': 'restore_matrix', 'postgrest-results.jsonl': 'postgrest',
    'property-generated-sql.jsonl': 'properties', 'mutation-generated-sql.jsonl': 'mutations',
    'stale-return-results.jsonl': 'stale_return',
    'standalone-results.jsonl': 'standalone', 'docs-quickstart-results.jsonl': 'docs_quickstart',
    'upgrade-docs-results.jsonl': 'upgrade_docs', 'public-contract-results.jsonl': 'public_contract',
}


def verify(path: Path, suite: str | None = None) -> int:
    selected = suite or FILENAMES.get(path.name)
    if selected is None:
        raise RuntimeError(f"no reviewed inventory selected for {path}")
    rows = validate(result_rows(path), selected)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--suite", help="Reviewed inventory; required for non-standard filenames")
    args = parser.parse_args()
    for path in args.paths:
        if not args.suite and path.name not in FILENAMES:
            raise RuntimeError(f"no reviewed inventory selected for {path}")
        print(f"{path}: {verify(path, args.suite)} PASS results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
