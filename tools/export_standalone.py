#!/usr/bin/env python3
"""Export a new standalone bundle with the required implementation notice."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from verify_generated_sql_notice import verify_bundle

ROOT = Path(__file__).resolve().parents[1]


def export(psql: str, database: str, view: str, spec: Path, output: Path) -> None:
    specification = json.loads(spec.read_text())
    if not isinstance(specification, dict):
        raise ValueError("specification must be an object")
    result = subprocess.run([psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database,
                             "-v", "view=" + view, "-v", "spec=" + json.dumps(specification)],
                            input="SELECT jsontailor.duality_view_ddl(:'view', :'spec'::jsonb);\n",
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr)
    output.mkdir(parents=False, exist_ok=False)
    (output / "generated.sql").write_text(result.stdout)
    shutil.copyfile(ROOT / "GENERATED_SQL_NOTICE", output / "GENERATED_SQL_NOTICE")
    verify_bundle(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.psql, args.database, args.view, args.spec, args.output)
    print("standalone bundle: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
