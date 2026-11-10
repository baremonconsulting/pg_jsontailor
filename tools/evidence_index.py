#!/usr/bin/env python3
"""Digest-bound evidence bundles, approved indexes and durable retention.

Approval is an independently reviewed file from the protected evidence store,
not a field supplied by the producer. Main-branch authority is a separate gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads, result_rows, validate
from tools.verify_test_coverage import attest, MANIFEST

CI_FILES = {
    'helper-acl-results.jsonl': 'helper_acl',
    'runtime-limits-results.jsonl': 'runtime_limits',
    'rc02-database-results.jsonl': 'rc02_database',
    'rc02-read-results.jsonl': 'rc02_read',
    'core-results.jsonl': 'core', 'validation-results.jsonl': 'validation',
    'integrity-results.jsonl': 'integrity', 'repeatability-results.jsonl': 'repeatability',
    'subtransaction-results.jsonl': 'subtransaction', 'merge-results.jsonl': 'merge',
    'type-results.jsonl': 'types', 'shape-results.jsonl': 'shapes',
    'security-results.jsonl': 'security', 'partition-results.jsonl': 'partitions',
    'partition-restore-results.jsonl': 'partition_restore',
    'restore-matrix-results.jsonl': 'restore_matrix',
    'stale-return-results.jsonl': 'stale_return',
    'standalone-results.jsonl': 'standalone',
    'docs-quickstart-results.jsonl': 'docs_quickstart',
    'upgrade-docs-results.jsonl': 'upgrade_docs',
    'public-contract-results.jsonl': 'public_contract',
}
HTTP_FILES = {'postgrest-results.jsonl': 'postgrest',
              'http-security-results.jsonl': 'http_security',
              'http-concurrency-results.jsonl': 'http_concurrency',
              'synthetic-pilot-results.jsonl': 'synthetic_pilot'}


def ci_files(major, event):
    """HTTP is required on PG18 and on every scheduled all-major campaign."""
    require(major in {'17', '18', '19'}, 'invalid CI PostgreSQL major')
    require(event in {'push', 'pull_request', 'workflow_dispatch', 'schedule'},
            'invalid CI event')
    return CI_FILES | (HTTP_FILES if major == '18' or event == 'schedule' else {})


NIGHTLY_FILES = {'property-generated-sql.jsonl': 'properties',
                 'mutation-generated-sql.jsonl': 'mutations'}
OUTPUTS = ('regression-basic.out', 'isolation-concurrency.out',
           'isolation-pgjt_43_concurrency.out', 'isolation-pgjt_47_partition.out')
WORKFLOWS = {'ci': '.github/workflows/ci.yml',
             'nightly': '.github/workflows/nightly-generated-sql.yml'}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contained(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute(), "invalid evidence path")
    path = (root / name).resolve()
    require(path.is_relative_to(root.resolve()) and '..' not in Path(name).parts,
            "evidence path escapes its store")
    require(path.is_file() and path.stat().st_size > 0, f"missing/empty evidence file: {name}")
    return path


def check_file(root, entry):
    require(isinstance(entry, dict) and set(entry) == {'path', 'sha256'}, "invalid digest entry")
    path = contained(root, entry['path'])
    require(re.fullmatch('[0-9a-f]{64}', entry['sha256']) is not None,
            "invalid evidence digest")
    require(digest(path) == entry['sha256'], f"evidence digest mismatch: {entry['path']}")
    return path


def bundle_files(kind, major, event):
    if kind == 'ci':
        http = bool(set(ci_files(major, event)) & set(HTTP_FILES))
        return set(ci_files(major, event)) | set(OUTPUTS) | ({'postgrest-coverage.json'} if http else set()) | {'environment.json', 'coverage.json',
                                              'setbased-results.json', 'installation.log.p7m', 'postgresql.log.p7m',
                                              'suite-diagnostics.tar.p7m'}
    require(kind == 'nightly', "unknown evidence bundle kind")
    return set(NIGHTLY_FILES) | {'environment.json', 'installation.log.p7m', 'postgresql.log.p7m',
                               'suite-diagnostics.tar.p7m'}


def verify_bundle(path, root, sha):
    bundle = loads(path.read_text())
    require(set(bundle) == {'schema_version', 'kind', 'postgresql', 'run', 'files'}, "invalid bundle schema")
    require(bundle['schema_version'] == 1, "unsupported evidence bundle version")
    kind, major, run = bundle['kind'], bundle['postgresql'], bundle['run']
    require(major in ({'17', '18', '19'} if kind == 'ci' else {'18'}), "invalid bundle PostgreSQL major")
    require(set(run) == {'sha', 'workflow_id', 'workflow_path', 'event', 'ref', 'run_id', 'attempt'},
            "invalid workflow provenance schema")
    require(run['sha'] == sha and run['workflow_path'] == WORKFLOWS[kind], "workflow/SHA provenance differs")
    require(all(type(run[k]) is int and run[k] > 0 for k in ['workflow_id', 'run_id', 'attempt']),
            "workflow/run/attempt identity missing")
    require(run['event'] in ({'push', 'pull_request', 'workflow_dispatch', 'schedule'} if kind == 'ci'
                            else {'schedule', 'workflow_dispatch'}), "untrusted evidence event")
    require(isinstance(run['ref'], str) and run['ref'].startswith('refs/'), "missing evidence ref")
    files = bundle['files']
    require(isinstance(files, list), "invalid evidence files")
    names = [Path(item['path']).name for item in files]
    require(len(names) == len(set(names)) and set(names) == bundle_files(kind, major, run['event']), "bundle file inventory differs")
    checked = {Path(item['path']).name: check_file(root, item) for item in files}
    suites = ci_files(major, run['event']) if kind == 'ci' else NIGHTLY_FILES
    for name, suite in suites.items():
        rows = validate(result_rows(checked[name]), suite)
        if suite == 'standalone':
            require(all(row.get('source_commit') == sha and row.get('postgresql') == major
                        and re.fullmatch('[0-9a-f]{64}', row.get('generated_sql_sha256', ''))
                        for row in rows), "standalone exact-SHA/major/DDL provenance differs")
        if suite == 'stale_return':
            require(all(row.get('postgresql') == major and row.get('mutant_killed') is True
                        and row.get('mutant_compiled') is True and row.get('control_passed') is True
                        for row in rows), "missing stale-return mutant witness")
        if suite == 'properties':
            require(all(row.get('seed') == 50050 and row.get('cases') == 24 for row in rows),
                    "nightly property seed/case inventory differs")
        if suite == 'mutations':
            require(all(row.get('mutant_compiled') is True and row.get('mutant_killed') is True
                        and type(row.get('operator_matches')) is int and row['operator_matches'] > 0
                        for row in rows), "nightly mutant witness missing")
    environment = loads(checked['environment.json'].read_text())
    require(environment.get('run') == run and environment.get('postgresql') == major,
            "environment provenance differs")
    if kind == 'ci':
        coverage = loads(checked['coverage.json'].read_text())
        expected_coverage = attest(loads(MANIFEST.read_text()), 'postgresql', major,
                                  {suite: checked[name] for name, suite in CI_FILES.items()},
                                  {'installcheck', 'dump_restore'})
        require(coverage == expected_coverage, "coverage attestation differs")
        if set(suites) & set(HTTP_FILES):
            expected_http = attest(loads(MANIFEST.read_text()), 'postgrest', major,
                                   {suite: checked[name] for name, suite in HTTP_FILES.items()}, set())
            require(loads(checked['postgrest-coverage.json'].read_text()) == expected_http,
                    'HTTP coverage attestation differs')
        setbased = loads(checked['setbased-results.json'].read_text())
        expected_setbased = {'add': '100:5050', 'update': '100:5150',
                            'delete': '50:1325', 'insert': '100:5050'}
        require(setbased.get('status') == 'PASS' and setbased.get('expected') == expected_setbased
                and setbased.get('observed') == expected_setbased,
                "set-based operation suite failed")
        expected_outputs = {'regression-basic.out': 'tests/expected/basic.out',
                            **{f'isolation-{name}.out': f'tests/expected/{name}.out'
                               for name in ['concurrency', 'pgjt_43_concurrency', 'pgjt_47_partition']}}
        for name, expected in expected_outputs.items():
            require(checked[name].read_bytes() == (Path(__file__).resolve().parents[1] / expected).read_bytes(),
                    f"regression/isolation output differs: {name}")
    return bundle


def verify(index_path, sha, root, approval_path):
    require(re.fullmatch('[0-9a-f]{40}', sha) is not None and sha != '0' * 40, "invalid exact SHA")
    index = loads(index_path.read_text())
    require(set(index) == {'schema_version', 'commit', 'archive', 'bundles', 'artifacts'}, "invalid evidence index schema")
    require(index['schema_version'] == 1 and index['commit'] == sha, "evidence index version/SHA differs")
    check_file(root, index['archive'])
    require(isinstance(index['bundles'], list) and len(index['bundles']) == 4,
            "evidence index must contain CI17/18/19 and nightly18")
    bundles = [verify_bundle(check_file(root, entry), root, sha) for entry in index['bundles']]
    require(isinstance(index['artifacts'], list) and len(index['artifacts']) == 8,
            "downloaded GitHub artifact inventory differs")
    artifact_bundles = set()
    artifact_ids = set()
    for artifact in index['artifacts']:
        require(set(artifact) == {'bundle_sha256', 'access', 'github_artifact_id', 'download'}, "invalid GitHub artifact schema")
        bundle_digest = artifact['bundle_sha256']
        access = artifact['access']
        require(access in {'public', 'diagnostics'}, "invalid artifact access class")
        require(type(artifact['github_artifact_id']) is int and artifact['github_artifact_id'] > 0,
                "GitHub artifact ID missing")
        require((bundle_digest, access) not in artifact_bundles and artifact['github_artifact_id'] not in artifact_ids,
                "duplicate GitHub artifact/bundle")
        artifact_bundles.add((bundle_digest, access)); artifact_ids.add(artifact['github_artifact_id'])
        matches = [entry for entry in index['bundles'] if entry['sha256'] == bundle_digest]
        require(len(matches) == 1, "GitHub artifact is not bound to one indexed bundle")
        manifest = check_file(root, matches[0])
        bundle = loads(manifest.read_text())
        expected = {'bundle.json': manifest.read_bytes()} if access == 'public' else {}
        for entry in bundle['files']:
            path = check_file(root, entry)
            require(path.is_relative_to(manifest.parent.resolve()), "bundle files escape artifact directory")
            relative = path.relative_to(manifest.parent.resolve())
            require(relative.parts[0] in {'public', 'diagnostics'}, "file has no evidence access class")
            if relative.parts[0] == access:
                expected[str(relative if access == 'public' else Path(*relative.parts[1:]))] = path.read_bytes()
        with zipfile.ZipFile(check_file(root, artifact['download'])) as archive:
            members = [item.filename for item in archive.infolist() if not item.is_dir()]
            require(len(members) == len(set(members)), "duplicate GitHub ZIP member")
            require(set(members) in (set(expected), set(expected) | {'collection-status.json'}),
                    "GitHub artifact ZIP inventory differs")
            require(all(archive.read(name) == content for name, content in expected.items()),
                    "GitHub artifact content differs from bundle digest inventory")
            if 'collection-status.json' in members:
                require(loads(archive.read('collection-status.json')) == {'missing': []},
                        "GitHub artifact collection was incomplete")
    require({(b['kind'], b['postgresql']) for b in bundles} ==
            {('ci', '17'), ('ci', '18'), ('ci', '19'), ('nightly', '18')}, "duplicate/missing evidence bundle")
    ci_runs = [b['run'] for b in bundles if b['kind'] == 'ci']
    require(len({tuple(r[k] for k in ['workflow_id', 'run_id', 'attempt', 'event', 'ref']) for r in ci_runs}) == 1,
            "CI majors are not one coherent run attempt")
    require(all(b['run']['ref'] == ci_runs[0]['ref'] for b in bundles), "nightly ref differs from CI ref")
    approval = loads(approval_path.read_text())
    require(set(approval) == {'schema_version', 'index_sha256', 'approver', 'approved_at'}, "invalid evidence approval")
    require(approval['schema_version'] == 1 and approval['index_sha256'] == digest(index_path),
            "approval does not bind this evidence index")
    require(isinstance(approval['approver'], str) and approval['approver'].strip(), "named evidence approver missing")
    approved = datetime.fromisoformat(approval['approved_at'].replace('Z', '+00:00'))
    require(approved.tzinfo is not None, "approval timestamp must include a timezone")
    return index


def build(sha, archive, bundle_paths, root, output, artifacts):
    def entry(path):
        return {'path': str(path.resolve().relative_to(root.resolve())), 'sha256': digest(path)}
    bundles = [verify_bundle(path, root, sha) for path in bundle_paths]
    require(len(bundles) == 4 and {(b['kind'], b['postgresql']) for b in bundles} ==
            {('ci', '17'), ('ci', '18'), ('ci', '19'), ('nightly', '18')}, "incomplete index build")
    index = {'schema_version': 1, 'commit': sha, 'archive': entry(archive),
             'bundles': [entry(path) for path in bundle_paths], 'artifacts': artifacts}
    output.write_text(json.dumps(index, sort_keys=True, indent=2) + '\n')


def retain(index_path, sha, root, approval_path, destination):
    index = verify(index_path, sha, root, approval_path)
    require(not destination.exists(), "durable destination already exists; never overwrite evidence")
    destination.mkdir(mode=0o700, parents=True)
    entries = [index['archive'], *index['bundles']]
    entries.extend(item['download'] for item in index['artifacts'])
    for bundle in index['bundles']:
        entries.extend(loads(check_file(root, bundle).read_text())['files'])
    for item in entries:
        target = destination / item['path']
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(check_file(root, item), target)
        target.chmod(0o600)
    shutil.copyfile(index_path, destination / 'evidence-index.json')
    shutil.copyfile(approval_path, destination / 'approval.json')
    verify(destination / 'evidence-index.json', sha, destination, destination / 'approval.json')
    return destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['verify', 'build', 'retain'])
    parser.add_argument('index', type=Path)
    parser.add_argument('--sha', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--bundle', type=Path, action='append', default=[])
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--artifact-manifest', type=Path,
                        help='Reviewed array binding bundle digests to GitHub artifact IDs and downloaded ZIP digests')
    args = parser.parse_args()
    if args.command == 'build':
        require(args.archive is not None, '--archive is required')
        require(args.artifact_manifest is not None, '--artifact-manifest is required')
        build(args.sha, args.archive, args.bundle, args.root, args.index,
              loads(args.artifact_manifest.read_text()))
    else:
        require(args.approval is not None, 'independent protected-store --approval is required')
        if args.command == 'retain':
            require(args.destination is not None, '--destination is required')
            retain(args.index, args.sha, args.root, args.approval, args.destination)
        else:
            verify(args.index, args.sha, args.root, args.approval)
    print('evidence index: PASS')


if __name__ == '__main__':
    main()
