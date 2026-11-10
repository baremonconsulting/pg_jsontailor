#!/usr/bin/env python3
"""Verify public version metadata and current-only benchmark policy."""
from __future__ import annotations
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    def text(path):
        return (ROOT / path).read_text()
    version = re.search(r"^default_version = '([^']+)'$", text('pg_jsontailor.control'), re.M)
    require(version is not None, 'default_version is missing')
    value = version.group(1)
    require(f'version: {value}' in text('CITATION.cff'), 'citation/control version differs')
    require(f'## {value} —' in text('CHANGELOG.md'), 'changelog/control version differs')
    require((ROOT / f'sql/pg_jsontailor--{value}.sql').is_file(), 'versioned SQL missing')
    require(f'DATA = sql/pg_jsontailor--{value}.sql' in text('Makefile'), 'Makefile/control version differs')
    require('Final exact-SHA measurements' in text('docs/BENCHMARKS.md'), 'current-only benchmark policy missing')
    for path in ['README.md', 'docs/BENCHMARKS.md', '.github/workflows/ci.yml']:
        require('bench/reference/azure-' not in text(path) and '49.782 ms' not in text(path),
                'historical benchmark dependency/claim: ' + path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
