#!/usr/bin/env python3
"""Verify non-publishable benchmark diagnostic evidence."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.diagnostic_evidence import verify_diagnostic

parser = argparse.ArgumentParser()
parser.add_argument("folder", type=Path)
parser.add_argument("--candidate", required=True)
parser.add_argument("--trusted-key", required=True, type=Path)
parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
arguments = parser.parse_args()
print(json.dumps(verify_diagnostic(arguments.folder, candidate=arguments.candidate,
                                   trusted_key=arguments.trusted_key, repo=arguments.repo), sort_keys=True))
