#!/usr/bin/env python3
"""Execute the README quick start verbatim in its own disposable database."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RESULT_IDS = ("PGJT-65/readme-quickstart",)


def walkthrough(readme: str) -> str:
    section = readme.split("<!-- quickstart-smoke:start -->", 1)[1].split(
        "<!-- quickstart-smoke:end -->", 1)[0]
    return section.split("```sh\n", 1)[1].split("```", 1)[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    env = os.environ.copy()
    env["PATH"] = str(args.pg_bin.resolve()) + os.pathsep + env["PATH"]
    env["PGDATABASE"] = args.maintenance_database
    result = subprocess.run(["bash", "-c", walkthrough((ROOT / "README.md").read_text())],
                            cwd=ROOT, env=env, text=True, capture_output=True, timeout=90)
    expected = ("quickstart/read=new", "quickstart/update=PASS", "quickstart/delete=PASS",
                "quickstart/cleanup=PASS")
    passed = result.returncode == 0 and all(value in result.stdout for value in expected)
    row = {"name": RESULT_IDS[0], "status": "PASS" if passed else "FAIL",
           "observed": result.stdout, "diagnostics": result.stderr}
    if args.output:
        args.output.write_text(json.dumps(row, sort_keys=True) + "\n")
    if not passed:
        raise RuntimeError(f"README quick start failed: {row}")
    print(RESULT_IDS[0] + ": PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
