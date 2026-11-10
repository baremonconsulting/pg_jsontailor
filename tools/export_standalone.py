#!/usr/bin/env python3
"""Export a new standalone bundle with the required implementation notice."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from verify_generated_sql_notice import bundle_preamble, verify_bundle, verify_generator

ROOT = Path(__file__).resolve().parents[1]


def export(psql: str, database: str, view: str, spec: Path, output: Path) -> None:
    specification = json.loads(spec.read_text())
    if not isinstance(specification, dict):
        raise ValueError("specification must be an object")
    result = subprocess.run([psql, "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", database,
                             "-v", "view=" + view, "-v", "spec=" + json.dumps(specification)],
                            input="""BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL search_path = pg_catalog, pg_temp;
SELECT pg_catalog.json_build_object(
 'generator_version', e.extversion,
 'generator_postgresql_major', pg_catalog.current_setting('server_version_num')::integer / 10000,
 'generator_hash', pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
   pg_catalog.pg_get_functiondef('jsontailor.duality_view_ddl(text,jsonb)'::regprocedure),
   'UTF8')),'hex'),
 'ddl', jsontailor.duality_view_ddl(:'view', :'spec'::jsonb))
FROM pg_catalog.pg_extension e WHERE e.extname='pg_jsontailor';
COMMIT;
""",
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr)
    # Quiet psql framing is required because the JSON is the sole data record.
    generated = json.loads(result.stdout)
    verify_generator(generated['generator_version'], generated['generator_postgresql_major'],
                     generated['generator_hash'])
    notice = (ROOT / "GENERATED_SQL_NOTICE").read_text()
    preamble = bundle_preamble(generated['generator_version'],
                               generated['generator_postgresql_major'], view, notice,
                               generated['generator_hash'])
    if not isinstance(generated['ddl'], str) or not generated['ddl'].strip():
        raise RuntimeError("generator returned no standalone SQL")
    output.mkdir(parents=False, exist_ok=False)
    (output / "generated.sql").write_text(preamble + generated['ddl'].rstrip() + '\n\nCOMMIT;\n')
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
