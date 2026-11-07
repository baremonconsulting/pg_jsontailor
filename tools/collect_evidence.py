#!/usr/bin/env python3
"""Collect available evidence on success or failure; require exact completeness."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tarfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evidence_index import ci_files, NIGHTLY_FILES, WORKFLOWS, bundle_files, digest, verify_bundle
from tools.redact_results import redacted_rows


def encrypt(path, target):
    certificate = os.environ.get('EVIDENCE_DIAGNOSTICS_CERTIFICATE', '')
    if not certificate:
        raise RuntimeError('release-owner diagnostics certificate is not configured')
    with tempfile.TemporaryDirectory(prefix='pgjt-recipient-') as temporary:
        recipient = Path(temporary) / 'recipient.pem'
        recipient.write_text(certificate)
        encrypted = subprocess.run(['openssl', 'cms', '-encrypt', '-binary', '-aes-256-cbc',
            '-outform', 'DER', '-in', str(path), '-out', str(target), str(recipient)],
            capture_output=True, text=True, timeout=15)
        if encrypted.returncode:
            target.unlink(missing_ok=True)
            raise RuntimeError('diagnostics encryption failed')


def collect(kind, major, source, repo, output, workflow_id):
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    public = output / 'public'; public.mkdir(mode=0o700, exist_ok=True)
    restricted = output / 'diagnostics'; restricted.mkdir(mode=0o700, exist_ok=True)
    run = {'sha': os.environ['GITHUB_SHA'], 'workflow_id': workflow_id,
           'workflow_path': WORKFLOWS[kind], 'event': os.environ['GITHUB_EVENT_NAME'],
           'ref': os.environ['GITHUB_REF'], 'run_id': int(os.environ['GITHUB_RUN_ID']),
           'attempt': int(os.environ['GITHUB_RUN_ATTEMPT'])}
    environment = {'postgresql': major, 'run': run, 'platform': platform.platform(),
                   'python': platform.python_version()}
    psql = os.environ.get('PGBIN', f'/usr/lib/postgresql/{major}/bin') + '/psql'
    try:
        version = subprocess.run([psql, '--version'], capture_output=True, text=True, timeout=10)
        environment['psql_version'] = version.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        environment['psql_version'] = 'NOT_AVAILABLE'
    (public / 'environment.json').write_text(json.dumps(environment, sort_keys=True) + '\n')
    suites = ci_files(major, run['event']) if kind == 'ci' else NIGHTLY_FILES
    sources = {name: source / name.removesuffix('.p7m') for name in
               bundle_files(kind, major, run['event']) - {'environment.json', 'suite-diagnostics.tar.p7m'}}
    if kind == 'ci':
        sources['coverage.json'] = source / f'coverage-pg{major}.json'
        sources.update({'regression-basic.out': repo / 'tests/output/results/basic.out',
                        **{f'isolation-{name}.out': repo / f'tests/output_iso/results/{name}.out'
                           for name in ['concurrency', 'pgjt_43_concurrency', 'pgjt_47_partition']}})
    files = []
    missing = []
    for name, path in sorted(sources.items()):
        if path.is_file() and path.stat().st_size:
            target = (restricted if name.endswith('.p7m') else public) / name
            if name.endswith('.p7m'):
                try:
                    encrypt(path, target)
                except RuntimeError as error:
                    missing.append(name + ': ' + str(error)); continue
            elif name in suites:
                suite = suites[name]
                try:
                    redacted = redacted_rows(path, suite)
                except (RuntimeError, ValueError) as error:
                    missing.append(name + ': invalid suite evidence'); continue
                target.write_text(redacted)
            else:
                shutil.copyfile(path, target)
            target.chmod(0o600)
        else:
            missing.append(str(path))
    for path in sorted([*restricted.iterdir(), *public.iterdir()]):
        files.append({'path': str(path.relative_to(output.parent)), 'sha256': digest(path)})
    try:
        with tempfile.TemporaryDirectory(prefix='pgjt-raw-evidence-') as temporary:
            raw_archive = Path(temporary) / 'suites.tar'
            with tarfile.open(raw_archive, 'w') as archive:
                for name, path in sources.items():
                    if path.is_file() and name.endswith(('.jsonl', '.json')):
                        archive.add(path, arcname=name, recursive=False)
                for name in ['postgrest.log', 'postgrest-transcript.md', 'postgrest-results.jsonl',
                             'postgrest-coverage.json']:
                    path = source / name
                    if path.is_file():
                        archive.add(path, arcname=name, recursive=False)
            encrypted = restricted / 'suite-diagnostics.tar.p7m'
            encrypt(raw_archive, encrypted)
            encrypted.chmod(0o600)
            files.append({'path': str(encrypted.relative_to(output.parent)), 'sha256': digest(encrypted)})
    except RuntimeError as error:
        missing.append('suite-diagnostics.tar.p7m: ' + str(error))
    bundle = {'schema_version': 1, 'kind': kind, 'postgresql': major, 'run': run, 'files': files}
    manifest = output / 'bundle.json'
    manifest.write_text(json.dumps(bundle, sort_keys=True, indent=2) + '\n')
    (output / 'collection-status.json').write_text(json.dumps({'missing': missing}) + '\n')
    if missing:
        raise RuntimeError('incomplete evidence inventory: ' + ', '.join(missing))
    verify_bundle(manifest, output.parent, run['sha'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=['ci', 'nightly'], required=True)
    parser.add_argument('--major', required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workflow-id', required=True, type=int)
    args = parser.parse_args()
    collect(args.kind, args.major, args.input, args.repo, args.output, args.workflow_id)


if __name__ == '__main__':
    main()
