#!/usr/bin/env python3
"""Publish only reviewed identifiers, outcomes and bounded provenance fields."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import result_rows, validate

SAFE_FIELDS = {'id', 'name', 'test_id', 'status', 'postgresql', 'source_commit',
               'generated_sql_sha256', 'seed', 'cases', 'operator_matches',
               'mutant_compiled', 'mutant_killed', 'control_passed'}


def redacted_rows(path, suite):
    rows = validate(result_rows(path), suite, allow_failures=True)
    return ''.join(json.dumps({key: value for key, value in row.items() if key in SAFE_FIELDS},
                              sort_keys=True) + '\n' for row in rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', required=True)
    args = parser.parse_args()
    args.output.write_text(redacted_rows(args.input, args.suite))


if __name__ == '__main__':
    main()
