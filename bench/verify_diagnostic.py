#!/usr/bin/env python3
"""Verify non-publishable benchmark diagnostic evidence."""
import argparse
import json
from pathlib import Path
from bench.diagnostic_evidence import verify_diagnostic

parser = argparse.ArgumentParser()
parser.add_argument("folder", type=Path)
arguments = parser.parse_args()
print(json.dumps(verify_diagnostic(arguments.folder), sort_keys=True))
