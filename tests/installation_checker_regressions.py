#!/usr/bin/env python3
"""Exercise installation verification under rendering and fault-injection cases."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess


def run(psql: str, database: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [psql, "-X", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=script, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    checker = Path(__file__).with_name("verify_installation.sql").read_text()

    quoted = run(args.psql, args.database,
                 "SET quote_all_identifiers=on;\n" + checker)
    if quoted.returncode:
        raise RuntimeError("quoted installation check failed:\n" + quoted.stderr)

    mutation = """BEGIN;
CREATE OR REPLACE FUNCTION jsontailor.duality_view_ddl(view_name text,spec jsonb)
RETURNS text LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path=pg_catalog,pg_temp
AS $mutated$ BEGIN RETURN 'SELECT false'; END $mutated$;
"""
    invalid = run(args.psql, args.database, mutation + checker)
    if invalid.returncode == 0 or "generator did not create the requested view" not in invalid.stderr:
        raise RuntimeError("invalid generator passed installation check:\n"
                           + invalid.stdout + invalid.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
