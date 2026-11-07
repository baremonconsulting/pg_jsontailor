"""Trusted fixtures exercise index integrity; fixtures are not live approval."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evidence_index import (CI_FILES, NIGHTLY_FILES, WORKFLOWS, bundle_files,
                                  build, digest, retain, verify, verify_bundle)
from tools.strict_results import inventories, loads
from tools.verify_test_coverage import attest, MANIFEST

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
            suites = CI_FILES if kind == 'ci' else NIGHTLY_FILES
            for name, suite in suites.items():
                field = 'id' if suite in {'core', 'validation', 'properties', 'mutations', 'stale_return'} else 'name'
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
                expected = {'add': '100:5050', 'update': '100:5150', 'delete': '50:1325', 'insert': '100:5050'}
                values['setbased-results.json'] = {'status': 'PASS', 'expected': expected, 'observed': expected}
            for name, value in values.items():
                (restricted / name).write_text(json.dumps(value))
            for name in bundle_files(kind) - set(suites) - set(values):
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
