"""Trusted fixtures exercise index integrity; fixtures are not live approval."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evidence_index import (CI_FILES, HTTP_FILES, ci_files, NIGHTLY_FILES, WORKFLOWS, bundle_files,
                                  build, digest, retain, verify, verify_bundle)
from tools.strict_results import inventories, loads
from tools.verify_test_coverage import attest, MANIFEST
from tools.verify_jsonl_results import verify as verify_jsonl
from tools.collect_evidence import collect

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


class EvidenceIndex(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundles = []
        self.artifacts = []
        for kind, major in [('ci', '17'), ('ci', '18'), ('ci', '19'), ('nightly', '18')]:
            folder = self.root / f'{kind}-pg{major}'
            folder.mkdir()
            restricted = folder / 'public'; restricted.mkdir()
            diagnostics = folder / 'diagnostics'; diagnostics.mkdir()
            run = {'sha': SHA, 'workflow_id': 101 if kind == 'ci' else 102,
                   'workflow_path': WORKFLOWS[kind], 'event': 'workflow_dispatch',
                   'ref': 'refs/heads/candidate', 'run_id': 201 if kind == 'ci' else 202, 'attempt': 1}
            suites = ci_files(major, run['event']) if kind == 'ci' else NIGHTLY_FILES
            for name, suite in suites.items():
                field = 'id' if suite in {'core', 'validation', 'properties', 'mutations', 'stale_return',
                                         'helper_acl', 'runtime_limits', 'rc02_database', 'postgrest',
                                         'http_security', 'http_concurrency', 'minimum_grants'} else 'name'
                rows = [{field: ident, 'status': 'PASS'} for ident in inventories()[suite]]
                for row in rows:
                    if suite == 'standalone':
                        row.update(source_commit=SHA, postgresql=major, generated_sql_sha256='b' * 64)
                    if suite in {'mutations', 'stale_return'}:
                        row.update(mutant_compiled=True, mutant_killed=True, operator_matches=1,
                                   control_passed=True, postgresql=major)
                    if suite == 'properties':
                        row.update(seed=50050, cases=24)
                (restricted / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
            values = {'environment.json': {'postgresql': major, 'run': run}}
            if kind == 'ci':
                values['coverage.json'] = attest(loads(MANIFEST.read_text()), 'postgresql', major,
                    {suite: restricted / name for name, suite in CI_FILES.items()}, {'installcheck', 'dump_restore'})
                if set(suites) & set(HTTP_FILES):
                    values['postgrest-coverage.json'] = attest(loads(MANIFEST.read_text()), 'postgrest', major,
                        {suite: restricted / name for name, suite in HTTP_FILES.items()}, set())
                expected = {'add': '100:5050', 'update': '100:5150', 'delete': '50:1325', 'insert': '100:5050'}
                values['setbased-results.json'] = {'status': 'PASS', 'expected': expected, 'observed': expected}
            for name, value in values.items():
                (restricted / name).write_text(json.dumps(value))
            for name in bundle_files(kind, major, run['event']) - set(suites) - set(values):
                content = 'restricted diagnostic fixture\n'
                if name == 'regression-basic.out':
                    content = (ROOT / 'tests/expected/basic.out').read_text()
                elif name.startswith('isolation-'):
                    content = (ROOT / 'tests/expected' / name.removeprefix('isolation-')).read_text()
                ((diagnostics if name.endswith('.p7m') else restricted) / name).write_text(content)
            bundle = {'schema_version': 1, 'kind': kind, 'postgresql': major, 'run': run,
                      'files': [{'path': str(path.relative_to(self.root)), 'sha256': digest(path)}
                                for path in sorted([*restricted.iterdir(), *diagnostics.iterdir()])]}
            manifest = folder / 'bundle.json'; manifest.write_text(json.dumps(bundle))
            self.bundles.append(manifest)
            for access in ['public', 'diagnostics']:
                downloaded = self.root / f'{kind}-pg{major}-{access}.zip'
                with zipfile.ZipFile(downloaded, 'w') as archive:
                    if access == 'public':
                        archive.write(manifest, 'bundle.json')
                    for path in (restricted if access == 'public' else diagnostics).iterdir():
                        archive.write(path, str(path.relative_to(folder if access == 'public' else diagnostics)))
                self.artifacts.append({'bundle_sha256': digest(manifest), 'access': access,
                                       'github_artifact_id': 301+len(self.artifacts),
                                       'download': {'path': downloaded.name, 'sha256': digest(downloaded)}})
        self.archive = self.root / 'candidate.tar.gz'; self.archive.write_bytes(b'release archive fixture')
        self.index = self.root / 'index.json'
        self.approval = self.root / 'approval.json'
        build(SHA, self.archive, self.bundles, self.root, self.index, self.artifacts)
        self.approve()

    def approve(self):
        self.approval.write_text(json.dumps({'schema_version': 1, 'index_sha256': digest(self.index),
                                           'approver': 'Fixture Reviewer', 'approved_at': '2026-11-07T12:00:00Z'}))

    def test_complete_approved_fixture_and_durable_read_back(self):
        verify(self.index, SHA, self.root, self.approval)
        destination = self.root / 'durable'
        retain(self.index, SHA, self.root, self.approval, destination)
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            retain(self.index, SHA, self.root, self.approval, destination)

    def test_security_runtime_and_http_policy_is_explicit(self):
        for major in ['17', '18', '19']:
            for event in ['push', 'pull_request', 'workflow_dispatch', 'schedule']:
                suites = ci_files(major, event)
                self.assertIn('helper-acl-results.jsonl', suites)
                self.assertIn('runtime-limits-results.jsonl', suites)
                self.assertEqual(set(HTTP_FILES).issubset(suites), major == '18' or event == 'schedule')
        for name, suite in ci_files('18', 'workflow_dispatch').items():
            self.assertEqual(verify_jsonl(self.root / 'ci-pg18/public' / name), len(inventories()[suite]))

    def test_new_suites_are_required_by_scope_attestation(self):
        major = '18'
        folder = self.root / 'ci-pg18/public'
        for name, suite in {**CI_FILES, **HTTP_FILES}.items():
            if suite not in {'helper_acl', 'runtime_limits', 'http_security', 'http_concurrency', 'postgrest'}:
                continue
            scope = 'postgrest' if name in HTTP_FILES else 'postgresql'
            source = HTTP_FILES if scope == 'postgrest' else CI_FILES
            results = {value: folder / key for key, value in source.items() if value != suite}
            with self.subTest(suite=suite), self.assertRaisesRegex(RuntimeError, 'runtime result was not supplied'):
                attest(loads(MANIFEST.read_text()), scope, major, results, {'installcheck', 'dump_restore'})

    def test_ci_collector_retains_and_redacts_every_required_new_suite(self):
        source = self.root / 'ci-pg18/public'
        for name in ['helper-acl-results.jsonl', 'runtime-limits-results.jsonl', *HTTP_FILES]:
            path = source / name
            rows = [loads(line) for line in path.read_text().splitlines()]
            for row in rows:
                row['detail'] = 'PRIVATE-PGJT76-SENTINEL'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        shutil.copyfile(source / 'coverage.json', source / 'coverage-pg18.json')
        for name in ['installation.log', 'postgresql.log']:
            (source / name).write_text('private log fixture')
        repo = self.root / 'collector-repo'
        for name in ['basic', 'concurrency', 'pgjt_43_concurrency', 'pgjt_47_partition']:
            output = repo / ('tests/output/results' if name == 'basic' else 'tests/output_iso/results') / (name + '.out')
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'tests/expected' / (name + '.out'), output)
        key, certificate = self.root / 'collector.key', self.root / 'collector.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-subj', '/CN=Collector Fixture', '-days', '1', '-keyout', str(key),
                        '-out', str(certificate)], check=True, capture_output=True, timeout=15)
        environment = {'GITHUB_SHA': SHA, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                       'GITHUB_REF': 'refs/heads/candidate', 'GITHUB_RUN_ID': '201',
                       'GITHUB_RUN_ATTEMPT': '1', 'PGBIN': '/nonexistent-fixture-prefix',
                       'EVIDENCE_DIAGNOSTICS_CERTIFICATE': certificate.read_text()}
        output = self.root / 'collected/ci-pg18'
        with mock.patch.dict(os.environ, environment):
            collect('ci', '18', source, repo, output, 101)
        verify_bundle(output / 'bundle.json', output.parent, SHA)
        for path in (output / 'public').iterdir():
            self.assertNotIn(b'PRIVATE-PGJT76-SENTINEL', path.read_bytes())
        for name in ['helper-acl-results.jsonl', 'runtime-limits-results.jsonl', *HTTP_FILES]:
            self.assertTrue((output / 'public' / name).is_file())

    def test_new_suites_cannot_be_omitted_truncated_failed_or_tampered(self):
        for name in ['helper-acl-results.jsonl', 'runtime-limits-results.jsonl', *HTTP_FILES]:
            manifest = self.bundles[1]  # PG18 must retain every HTTP suite.
            original = manifest.read_bytes()
            path = self.root / 'ci-pg18/public' / name
            content = path.read_bytes()
            for change in ['missing', 'truncated', 'failed', 'tampered']:
                with self.subTest(suite=name, change=change):
                    bundle = loads(original)
                    entry = next(item for item in bundle['files'] if item['path'].endswith('/' + name))
                    if change == 'missing':
                        bundle['files'].remove(entry)
                    else:
                        rows = [loads(line) for line in content.decode().splitlines()]
                        if change == 'truncated': rows.pop()
                        elif change == 'failed': rows[0]['status'] = 'FAIL'
                        else: rows[0]['id'] = 'tampered-id'
                        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                        entry['sha256'] = digest(path)  # Rehashing must not bypass inventory/semantics.
                    manifest.write_text(json.dumps(bundle))
                    with self.assertRaises(RuntimeError):
                        verify_bundle(manifest, self.root, SHA)
                    path.write_bytes(content)
                    manifest.write_bytes(original)

    def test_changed_file_archive_approval_sha_or_attempt_cannot_pass(self):
        for change in ['file', 'archive', 'approval', 'sha', 'missing-major', 'missing-nightly', 'attempt', 'artifact']:
            with self.subTest(change=change):
                candidate = copy.deepcopy(loads(self.index.read_text()))
                if change in {'file', 'archive'}:
                    path = self.archive if change == 'archive' else self.root / 'ci-pg17/public/core-results.jsonl'
                    old = path.read_bytes(); path.write_bytes(old + b'changed')
                    with self.assertRaises(RuntimeError):
                        verify(self.index, SHA, self.root, self.approval)
                    path.write_bytes(old); continue
                if change == 'approval':
                    self.approval.write_text('{}')
                elif change == 'sha':
                    candidate['commit'] = 'c' * 40
                elif change.startswith('missing'):
                    candidate['bundles'].pop(0 if change == 'missing-major' else -1)
                elif change == 'artifact':
                    candidate['artifacts'][0]['download']['sha256'] = '0' * 64
                else:
                    manifest = self.bundles[0]; old = manifest.read_bytes()
                    value = loads(old); value['run']['attempt'] = 2; manifest.write_text(json.dumps(value))
                original = self.index.read_bytes()
                self.index.write_text(json.dumps(candidate))
                if change != 'approval': self.approve()
                with self.assertRaises(RuntimeError):
                    verify(self.index, SHA, self.root, self.approval)
                self.index.write_bytes(original)
                if change == 'attempt': manifest.write_bytes(old)
                self.approve()

    def test_even_rehashed_bundle_rejects_truncation_extra_and_unreferenced_failure(self):
        manifest = self.bundles[0]
        bundle = loads(manifest.read_text())
        path = self.root / 'ci-pg17/public/core-results.jsonl'
        original = path.read_text()
        for content in [original.splitlines()[0]+'\n', original+'{"id":"extra","status":"PASS"}\n',
                        original+'{"id":"unreferenced","status":"FAIL"}\n']:
            path.write_text(content)
            for entry in bundle['files']:
                if entry['path'].endswith('/core-results.jsonl'):
                    entry['sha256'] = digest(path)
            manifest.write_text(json.dumps(bundle))
            with self.assertRaises(RuntimeError):
                verify_bundle(manifest, self.root, SHA)
        path.write_text(original)


if __name__ == '__main__':
    unittest.main()
