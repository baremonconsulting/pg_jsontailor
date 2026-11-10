#!/usr/bin/env python3
"""Install an immutable candidate into an explicitly disposable PostgreSQL prefix."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.installation import candidate_files, require, sha256, sign, verify_attestation

PROBE_SCHEMA = """
CREATE SCHEMA jt_install_probe;
CREATE TABLE jt_install_probe.refs(id bigint PRIMARY KEY,label text);
CREATE TABLE jt_install_probe.roots(id bigint PRIMARY KEY,ref bigint REFERENCES jt_install_probe.refs,label text);
CREATE TABLE jt_install_probe.children(id bigint PRIMARY KEY,parent bigint REFERENCES jt_install_probe.roots,label text);
CREATE INDEX ON jt_install_probe.children(parent,id);
"""
ROOT_SPEC = {'root': {'table': 'jt_install_probe.roots', 'key': 'id',
                     'fields': {'label': 'label'}, 'insert': True, 'update': True, 'delete': True}}
FULL_SPEC = {**ROOT_SPEC,
             'object': {'name': 'reference', 'table': 'jt_install_probe.refs', 'key': 'id',
                        'root_fk': 'ref', 'fields': {'id': 'id', 'label': 'label'}, 'etag': True},
             'array': {'name': 'children', 'table': 'jt_install_probe.children', 'key': 'id',
                       'parent_fk': 'parent', 'order_by': 'id', 'fields': {'id': 'id', 'label': 'label'},
                       'insert': True, 'update': True, 'delete': True}}


def now():
    return datetime.now(timezone.utc).isoformat()


def catalogue_and_ddl(sql):
    catalogue = sql("SELECT coalesce(jsonb_agg(jsonb_build_object('identity',p.oid::regprocedure::text,"
                    "'definition',pg_get_functiondef(p.oid)) ORDER BY p.oid::regprocedure::text),'[]') "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='jsontailor';")
    generated = {}
    for name, specification in [('root', ROOT_SPEC), ('object_array', FULL_SPEC)]:
        spec = json.dumps(specification).replace("'", "''")
        ddl = sql("SELECT jsontailor.duality_view_ddl('jt_install_probe.document','" + spec + "'::jsonb);")
        generated[name] = sha256(ddl.encode())
    return {'catalogue_sha256': sha256(catalogue.encode()), 'generated_ddl': generated}


def installed_files(pg_config, expected):
    sharedir = Path(subprocess.check_output([str(pg_config), '--sharedir'], text=True).strip())
    return {name: sha256((sharedir / 'extension' / Path(name).name).read_bytes()) for name in expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--pg-config', type=Path, required=True)
    parser.add_argument('--disposable-prefix', type=Path, required=True,
                        help='must exactly match pg_config --bindir parent; never use a shared installation')
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--trusted-key', type=Path, required=True)
    parser.add_argument('--signer', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), 'installation evidence directory already exists')
    args.output.mkdir(parents=True)
    trace, logs = [], []
    started = now()
    def run(argv, input=None):
        timestamp, start = now(), time.monotonic()
        result = subprocess.run([str(a) for a in argv], input=input, capture_output=True,
                                text=True, timeout=180, cwd=args.repo)
        trace.append({'argv': [str(a) for a in argv], 'started_at': timestamp,
                      'elapsed_seconds': time.monotonic() - start, 'returncode': result.returncode})
        logs.append(json.dumps(trace[-1]) + '\n' + result.stdout + result.stderr)
        (args.output / 'installation.log').write_text('\n'.join(logs))
        require(result.returncode == 0, 'installation command failed: ' + result.stderr[-1000:])
        return result.stdout
    candidate = run(['git', 'rev-parse', 'HEAD']).strip()
    require(not run(['git', 'status', '--porcelain']).strip(), 'candidate worktree must be immutable/clean')
    expected = candidate_files(args.repo, candidate)
    pg_config = args.pg_config.resolve()
    bindir = Path(run([pg_config, '--bindir']).strip())
    require(bindir.parent.resolve() == args.disposable_prefix.resolve(), 'disposable installation prefix differs')
    require(args.disposable_prefix.resolve() not in {
        Path('/'), Path('/usr'), Path('/usr/local'), Path.home().resolve()},
            'refusing a shared installation prefix')
    run(['make', 'install', f'PG_CONFIG={pg_config}'])
    observed_files = installed_files(pg_config, expected)
    require(observed_files == expected, 'installed files differ from exact candidate')
    databases = ['pgjt_attest_' + uuid.uuid4().hex for _ in range(2)]
    def sql(database, text):
        return run([bindir / 'psql', '-XqAt', '-v', 'ON_ERROR_STOP=1', '-d', database], text)
    try:
        for database in databases:
            run([bindir / 'createdb', database])
        sql(databases[0], 'CREATE EXTENSION pg_jsontailor;\n' + PROBE_SCHEMA)
        control = run(['git', 'show', f'{candidate}:pg_jsontailor.control'])
        version = re.search(r"^default_version = '([^']+)'$", control, re.M).group(1)
        source = run(['git', 'show', f'{candidate}:sql/pg_jsontailor--{version}.sql'])
        require(source.startswith('\\echo '), 'extension guard format differs')
        sql(databases[1], 'BEGIN; CREATE SCHEMA jsontailor;\n' + source.split('\n', 1)[1]
            + '\nCOMMIT;\n' + PROBE_SCHEMA)
        actual = catalogue_and_ddl(lambda text: sql(databases[0], text))
        oracle = catalogue_and_ddl(lambda text: sql(databases[1], text))
        require(actual == oracle, 'installed catalogue/generated DDL differs from exact-source oracle')
        version_string = sql(databases[0], 'SHOW server_version;').strip()
    finally:
        for database in databases:
            run([bindir / 'dropdb', '--if-exists', database])
    payload = {'schema_version': 1, 'candidate': candidate, 'signer': args.signer,
               'started_at': started, 'finished_at': now(), 'source_files': expected,
               'installed_files': observed_files, **actual,
               'expected_catalogue_sha256': oracle['catalogue_sha256'],
               'expected_generated_ddl': oracle['generated_ddl'], 'postgresql': version_string,
               'pg_config': str(pg_config), 'install_prefix': str(args.disposable_prefix.resolve()),
               'commands': trace, 'log_sha256': sha256((args.output / 'installation.log').read_bytes())}
    path = args.output / 'installation.json'
    path.write_text(json.dumps(sign(payload, args.private_key), indent=2) + '\n')
    verify_attestation(path, repo=args.repo, candidate=candidate, trusted_key=args.trusted_key)
    print(json.dumps({'status': 'PASS', 'candidate': candidate, 'attestation': str(path)}))


if __name__ == '__main__':
    main()
