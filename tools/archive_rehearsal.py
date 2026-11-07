#!/usr/bin/env python3
"""Exercise only extracted package contents in a disposable PostgreSQL cluster."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evidence_index import digest
from tools.release_authority import require
from tools.source_provenance import archive_provenance
from tools.strict_results import result_rows, validate
from tools.verify_generated_sql_notice import verify_archive


def extract(archive_path, destination):
    verify_archive(archive_path)
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            require(path.parts and not path.is_absolute() and '..' not in path.parts
                    and '.git' not in path.parts and (member.isfile() or member.isdir()), 'unsafe source archive member')
        archive.extractall(destination, filter='data')
        roots = list(destination.iterdir())
        require(len(roots) == 1 and roots[0].is_dir(), 'archive must extract to one source root')
        return roots[0]


def run(archive, sha, checksum, pg_bin, output):
    require(digest(archive) == checksum, 'archive checksum differs')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pgjt-archive-') as temporary:
        root = extract(archive, Path(temporary))
        archive_provenance(root, archive, sha, checksum)
        def command(*args):
            subprocess.run(list(map(str, args)), cwd=root, check=True, timeout=600)
        major = subprocess.check_output([str(pg_bin / 'psql'), '-XAt', '-c', 'SHOW server_version_num'], text=True).strip()[:2]
        require(major in {'17', '18', '19'}, 'archive PostgreSQL major unsupported')
        command('make', f'PG_CONFIG={pg_bin / "pg_config"}')
        command('sudo', 'make', f'PG_CONFIG={pg_bin / "pg_config"}', 'install')
        standalone = output / 'standalone-results.jsonl'
        command(sys.executable, '-B', 'tests/standalone_generated_sql.py', '--pg-bin', pg_bin,
                '--source-commit', sha, '--source-archive', archive, '--archive-sha256', checksum,
                '--output', standalone)
        validate(result_rows(standalone), 'standalone')
        command('make', f'PG_CONFIG={pg_bin / "pg_config"}', 'installcheck')
        command(pg_bin / 'createdb', 'archive_ci')
        try:
            command(pg_bin / 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-d', 'archive_ci',
                    '-c', 'CREATE EXTENSION pg_jsontailor')
            for script, suite in [('core_operations.py', 'core'), ('merge_operations.py', 'merge'),
                                  ('security_contract_matrix.py', 'security')]:
                results = output / f'{suite}-results.jsonl'
                args = [sys.executable, '-B', 'tests/' + script, '--psql', pg_bin / 'psql',
                        '--database', 'archive_ci', '--output', results]
                if suite == 'core':
                    args += ['--schema', 'examples/shop_schema.sql', '--spec', 'examples/orders_dv.spec.json']
                command(*args)
                validate(result_rows(results), suite)
        finally:
            command(pg_bin / 'dropdb', '--if-exists', 'archive_ci')
        summary = {'schema_version': 1, 'commit': sha, 'archive_sha256': checksum,
                   'postgresql': major, 'status': 'PASS',
                   'suites': ['notice', 'source-provenance', 'standalone', 'installcheck', 'core', 'merge', 'security']}
        (output / 'archive-rehearsal.json').write_text(json.dumps(summary, sort_keys=True) + '\n')
    print('extracted archive rehearsal: PASS')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--checksum', required=True)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.archive.resolve(), args.sha, args.checksum, args.pg_bin.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
