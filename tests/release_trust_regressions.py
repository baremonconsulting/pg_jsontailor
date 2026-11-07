"""Trusted release fixtures prove algorithms, never live release approval."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import release_authority as authority
from tools import release_metadata as metadata
from tools import release_record as records
from tools import repository_governance as governance
from tools.evidence_index import digest
from tools.strict_results import loads
from tools.verify_postpublication import verify as postverify
from tools.prepare_release import expect_candidate_denial

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40
POLICY = loads(authority.POLICY.read_text())


def snapshot(mode='main', ref='refs/heads/main'):
    names = ['nightly'] if mode == 'nightly' else (['ci'] if mode == 'candidate' else ['ci', 'codeql', 'dependency'])
    value = {'repository': POLICY['repository'], 'branch': {'name': 'main', 'protected': True, 'commit': {'sha': SHA}},
             'protection': {'enforce_admins': {'enabled': True}},
             'comparison': {'status': 'identical', 'base_commit': {'sha': SHA},
                            'merge_base_commit': {'sha': SHA}, 'commits': []}, 'workflows': {}}
    for offset, name in enumerate(names):
        expected = POLICY['workflows'][name]
        run = {'id': 200 + offset, 'run_attempt': 2, 'workflow_id': expected['id'], 'path': expected['path'],
               'head_sha': SHA, 'head_branch': ref.removeprefix('refs/heads/'),
               'event': 'push' if mode == 'main' else 'workflow_dispatch', 'status': 'completed',
               'conclusion': 'success', 'check_suite_id': 400 + offset}
        suite = {'id': run['check_suite_id'], 'head_sha': SHA, 'app': {'id': POLICY['actions_app_id']}}
        jobs, checks = [], []
        for index, job_name in enumerate(expected['jobs']):
            check_id = 1000 + 100 * offset + index
            jobs.append({'id': check_id + 5000, 'name': job_name, 'run_id': run['id'], 'run_attempt': 2,
                         'head_sha': SHA, 'status': 'completed', 'conclusion': 'success',
                         'check_run_url': f'https://api.github.com/repos/{POLICY["repository"]}/check-runs/{check_id}'})
            checks.append({'id': check_id, 'name': job_name, 'head_sha': SHA, 'app': suite['app'],
                           'check_suite': {'id': suite['id']}, 'status': 'completed', 'conclusion': 'success'})
        value['workflows'][name] = {'workflow': {**expected, 'state': 'active'}, 'run': run,
                                    'runs': [copy.deepcopy(run)], 'suite': suite, 'jobs': jobs, 'checks': checks}
    return value


class RunAuthority(unittest.TestCase):
    def test_candidate_expected_denial_never_accepts_outages_or_positive_main(self):
        def gate(message):
            def run(): raise RuntimeError(message)
            return run
        expect_candidate_denial(gate('ci: no authoritative main run for exact SHA/ref'))
        expect_candidate_denial(gate('SHA is not an ancestor of protected main'))
        with self.assertRaisesRegex(RuntimeError, 'unexpected reason'):
            expect_candidate_denial(gate('GitHub API failure'))
        with self.assertRaisesRegex(RuntimeError, 'unexpectedly satisfied'):
            expect_candidate_denial(lambda: None)

    def test_main_nightly_candidate_are_distinct(self):
        self.assertEqual(set(authority.verify(snapshot(), POLICY, SHA)), {'ci', 'codeql', 'dependency'})
        for mode in ['nightly', 'candidate']:
            ref = 'refs/heads/candidate'; value = snapshot(mode, ref)
            self.assertTrue(authority.verify(value, POLICY, SHA, mode, ref))
            with self.assertRaises(RuntimeError): authority.verify(value, POLICY, SHA)

    def test_adversaries(self):
        mutations = [
            (['repository'], 'other/repo'), (['branch', 'protected'], False),
            (['protection', 'enforce_admins', 'enabled'], False),
            (['comparison', 'merge_base_commit', 'sha'], 'b' * 40),
            (['workflows', 'ci', 'workflow', 'id'], 999),
            (['workflows', 'ci', 'workflow', 'path'], 'spoof.yml'),
            (['workflows', 'ci', 'suite', 'app', 'id'], 999),
            (['workflows', 'ci', 'suite', 'id'], 999),
            (['workflows', 'ci', 'run', 'head_sha'], 'b' * 40),
            (['workflows', 'ci', 'run', 'run_attempt'], 3),
            (['workflows', 'ci', 'jobs', 0, 'run_attempt'], 1),
            (['workflows', 'ci', 'jobs', 0, 'run_id'], 999),
            (['workflows', 'ci', 'jobs', 0, 'head_sha'], 'b' * 40),
            (['workflows', 'ci', 'jobs', 0, 'conclusion'], 'failure'),
            (['workflows', 'ci', 'jobs', 0, 'check_run_url'], 'https://other.invalid/check-runs/1000'),
            (['workflows', 'ci', 'checks', 0, 'conclusion'], 'failure')]
        for path, replacement in mutations:
            with self.subTest(path=path):
                value = snapshot(); target = value
                for key in path[:-1]: target = target[key]
                target[path[-1]] = replacement
                with self.assertRaises(RuntimeError): authority.verify(value, POLICY, SHA)
        for change in ['missing-job', 'duplicate-job', 'duplicate-check', 'duplicate-run', 'new-failed-run', 'dispatch', 'branch']:
            value = snapshot(); item = value['workflows']['ci']
            if change == 'missing-job': item['jobs'].pop()
            elif change.startswith('duplicate-'):
                key = {'duplicate-job': 'jobs', 'duplicate-check': 'checks', 'duplicate-run': 'runs'}[change]
                item[key].append(item[key][0])
            elif change == 'new-failed-run': item['runs'].append({**item['run'], 'id': 999, 'conclusion': 'failure'})
            else:
                key, replacement = ('event', 'workflow_dispatch') if change == 'dispatch' else ('head_branch', 'candidate')
                item['run'][key] = item['runs'][0][key] = replacement
            with self.subTest(change=change), self.assertRaises(RuntimeError): authority.verify(value, POLICY, SHA)

    def test_pagination_api_failure_count_conflict_and_duplicates(self):
        api = authority.GitHub(POLICY['repository']); first = [{'id': n} for n in range(100)]
        with patch.object(api, 'get', side_effect=[{'total_count': 101, 'jobs': first}, {'total_count': 101, 'jobs': [{'id': 100}]}]):
            self.assertEqual(len(api.pages('jobs', 'jobs')), 101)
        for pages in [[{'total_count': 101, 'jobs': first[:3]}],
                      [{'total_count': 101, 'jobs': first}, {'total_count': 102, 'jobs': [{'id': 100}]}],
                      [{'total_count': 101, 'jobs': first}, {'total_count': 101, 'jobs': [{'id': 0}]}]]:
            with patch.object(api, 'get', side_effect=pages), self.assertRaises(RuntimeError): api.pages('jobs', 'jobs')
        with patch.dict(os.environ, {'GH_AUTHORITY_TOKEN': 'fixture-read-only'}), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'API failure'): api.get('actions/runs')

    def test_authority_identity_never_falls_back_to_publication_or_saved_credentials(self):
        api = authority.GitHub(POLICY['repository'])
        for environment in [{}, {'GH_TOKEN': 'publication'}, {'GITHUB_TOKEN': 'publication'},
                            {'GH_AUTHORITY_TOKEN': '  ', 'GH_TOKEN': 'publication'}]:
            with self.subTest(environment=list(environment)), patch.dict(os.environ, environment, clear=True), \
                    patch('subprocess.run') as run:
                with self.assertRaisesRegex(RuntimeError, 'read-only authority credential is required'):
                    api.get('branches/main/protection')
                run.assert_not_called()
        with patch.dict(os.environ, {'GH_AUTHORITY_TOKEN': 'read-only', 'GH_TOKEN': 'publication',
                                     'GITHUB_TOKEN': 'publication'}, clear=True), \
                patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as run:
            api.get('branches/main/protection')
            environment = run.call_args.kwargs['env']
            self.assertEqual(environment['GH_TOKEN'], 'read-only')
            self.assertNotIn('GITHUB_TOKEN', environment)
            self.assertNotIn('read-only', ' '.join(run.call_args.args[0]))
        with self.assertRaisesRegex(RuntimeError, 'read-only'):
            governance.request('repos/example/repo', 'PUT', {}, authority=True)

    def test_index_consumption_and_authoritative_binding(self):
        sys.path.insert(0, str(ROOT / 'tests'))
        import evidence_trust_regressions as fixtures
        fixture = fixtures.EvidenceIndex('test_complete_approved_fixture_and_durable_read_back')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        identities = {'ci': loads(fixture.bundles[0].read_text())['run'],
                      'nightly': loads(fixture.bundles[-1].read_text())['run']}
        authority.verify_evidence(fixture.index, SHA, fixture.root, fixture.approval, identities)
        identities['ci']['attempt'] += 1
        with self.assertRaisesRegex(RuntimeError, 'authoritative run/attempt'):
            authority.verify_evidence(fixture.index, SHA, fixture.root, fixture.approval, identities)
        fixture.index.write_text('{}')
        with self.assertRaises(RuntimeError): authority.verify_evidence(fixture.index, SHA, fixture.root, fixture.approval, identities)


class MetadataStates(unittest.TestCase):
    def test_transitions_and_mixed_metadata(self):
        development = dict(schema_version=1, version='0.2.0', state='development', release_date=None)
        candidate = {**development, 'state': 'candidate'}
        final = {**candidate, 'state': 'final', 'release_date': '2026-11-07'}
        for before, after in [(development, development), (development, candidate), (candidate, final),
                              (final, {**development, 'version': '0.3.0'})]: metadata.transition(before, after)
        for before, after in [(final, candidate), (candidate, development), (development, final),
                              (final, {**final, 'release_date': '2026-11-08'}),
                              (final, {**candidate, 'version': '0.3.0'}), (final, {**development, 'version': '0.1.0'})]:
            with self.assertRaises(RuntimeError): metadata.transition(before, after)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ['release-metadata.json', 'README.md', 'CITATION.cff', 'CHANGELOG.md', 'pg_jsontailor.control']:
                (root / name).write_bytes((ROOT / name).read_bytes())
            metadata.verify(root)
            (root / 'release-metadata.json').write_text(json.dumps(final))
            with self.assertRaisesRegex(RuntimeError, 'heading differs'): metadata.verify(root)
            changelog = (root / 'CHANGELOG.md').read_text().replace('## 0.2.0 — unreleased', '## 0.2.0 — 2026-11-07')
            (root / 'CHANGELOG.md').write_text(changelog)
            with self.assertRaisesRegex(RuntimeError, 'README metadata state'): metadata.verify(root)


class SignedRecords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); cls.root = Path(cls.temporary.name)
        cls.private, cls.public = cls.root / 'private.pem', cls.root / 'public.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048',
                        '-out', str(cls.private)], check=True, capture_output=True)
        subprocess.run(['openssl', 'pkey', '-in', str(cls.private), '-pubout', '-out', str(cls.public)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls): cls.temporary.cleanup()

    def setUp(self):
        import tarfile
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.folder = Path(self.directory.name); self.archive = self.folder / 'pg_jsontailor-0.2.0.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in ['LICENSE', 'GENERATED_SQL_NOTICE', 'examples/standalone/README.md']:
                archive.add(ROOT / name, arcname='pg_jsontailor-0.2.0/' + name)
        self.checksum = self.folder / (self.archive.name + '.sha256')
        self.checksum.write_text(f'{digest(self.archive)}  {self.archive.name}\n')
        self.index = self.folder / 'index.json'; self.index.write_text('fixture index identity\n')
        self.record = {'schema_version': 1, 'kind': 'publication', 'commit': SHA, 'version': '0.2.0',
                       'evidence_index_sha256': digest(self.index), 'archive_sha256': digest(self.archive),
                       'signer': 'Fixture Signer', 'withdrawal_owner': 'Named Maintainer',
                       'created_at': '2026-11-07T12:00:00Z', 'release_approval': True,
                       'approvals': [{'reviewer': 'Evidence Reviewer', 'role': 'evidence-reviewer', 'approved_at': '2026-11-07T12:00:00Z'},
                                     {'reviewer': 'Release Reviewer', 'role': 'release-reviewer', 'approved_at': '2026-11-07T12:00:00Z'}]}
        self.path, self.signature = self.folder / 'record.json', self.folder / 'record.sig'
        self.trust = {'schema_version': 1, 'signers': [{'identity': self.record['signer'],
                      'public_key_sha256': digest(self.public), 'record_kinds': ['candidate', 'publication']}]}
        self.sign()

    def sign(self):
        self.path.write_text(json.dumps(self.record))
        records.crypt(['-sign', str(self.private), '-out', str(self.signature)], self.record)

    def verify(self, kind='publication'):
        return records.verify(self.path, self.signature, self.public, self.trust, SHA, self.index, self.archive, kind)

    def test_valid_candidate_is_not_release_approval(self):
        self.verify(); self.record.update(kind='candidate', release_approval=False); self.sign(); self.verify('candidate')
        with self.assertRaises(RuntimeError): self.verify('publication')

    def test_bad_signature_missing_approval_owner_and_trust(self):
        original = copy.deepcopy(self.record)
        for change in ['signature', 'approval', 'owner', 'key', 'index', 'candidate-approval', 'self-approval']:
            with self.subTest(change=change):
                self.record = copy.deepcopy(original); self.trust['signers'][0]['public_key_sha256'] = digest(self.public)
                if change == 'approval': self.record['approvals'] = []
                elif change == 'owner': self.record['withdrawal_owner'] = ''
                elif change == 'candidate-approval': self.record['kind'] = 'candidate'
                elif change == 'self-approval': self.record['approvals'][1]['reviewer'] = self.record['signer']
                elif change == 'index': self.record['evidence_index_sha256'] = '0' * 64
                self.sign()
                if change == 'signature': self.signature.write_bytes(b'corrupt')
                elif change == 'key': self.trust['signers'][0]['public_key_sha256'] = '0' * 64
                with self.assertRaises(RuntimeError): self.verify()

    def published(self):
        assets = [{'name': path.name, 'size': path.stat().st_size, 'digest': 'sha256:' + digest(path), 'state': 'uploaded'}
                  for path in [self.archive, self.checksum, self.path, self.signature]]
        return {'tag': {'ref': 'refs/tags/v0.2.0', 'object': {'type': 'commit', 'sha': SHA}},
                'release': {'tag_name': 'v0.2.0', 'draft': False, 'prerelease': False, 'immutable': True, 'assets': assets}}

    def postverify(self, value):
        return postverify(value, SHA, '0.2.0', self.archive, self.checksum, self.path, self.signature,
                          self.public, self.trust, self.index)

    def test_postpublication_positive_fixture_and_tampering(self):
        self.postverify(self.published())
        for change in ['tag', 'annotated-tag', 'mutable', 'draft', 'prerelease', 'missing-asset', 'extra-asset', 'digest', 'size', 'state']:
            with self.subTest(change=change):
                value = self.published(); release = value['release']
                if change == 'tag': value['tag']['object']['sha'] = 'b' * 40
                elif change == 'annotated-tag': value['tag']['object']['type'] = 'tag'
                elif change == 'mutable': release['immutable'] = False
                elif change in {'draft', 'prerelease'}: release[change] = True
                elif change == 'missing-asset': release['assets'].pop()
                elif change == 'extra-asset': release['assets'].append(release['assets'][0])
                elif change == 'digest': release['assets'][0]['digest'] = 'sha256:' + '0' * 64
                elif change == 'size': release['assets'][0]['size'] += 1
                else: release['assets'][0]['state'] = 'new'
                with self.assertRaises(RuntimeError): self.postverify(value)


class GovernancePolicy(unittest.TestCase):
    def test_environment_requires_independent_review_no_bypass_and_protected_refs(self):
        value = {'name': 'release', 'can_admins_bypass': False,
                 'protection_rules': [{'type': 'required_reviewers', 'prevent_self_review': True,
                                       'reviewers': [{'reviewer': {'id': 2}}]}],
                 'deployment_branch_policy': {'protected_branches': True, 'custom_branch_policies': False}}
        governance.verify_environment(value, 1)
        for change in ['bypass', 'self', 'owner-only', 'refs']:
            broken = copy.deepcopy(value)
            if change == 'bypass': broken['can_admins_bypass'] = True
            elif change == 'self': broken['protection_rules'][0]['prevent_self_review'] = False
            elif change == 'owner-only': broken['protection_rules'][0]['reviewers'][0]['reviewer']['id'] = 1
            else: broken['deployment_branch_policy']['protected_branches'] = False
            with self.assertRaises(RuntimeError): governance.verify_environment(broken, 1)

    def test_no_settings_write_without_verified_admin_and_owner(self):
        policy = loads((ROOT / '.github/governance-policy.json').read_text()); owner = policy['emergency_owner']
        value = {'repository': {'full_name': policy['repository'], 'permissions': {'admin': False},
                                'owner': {**owner, 'type': 'User'}}, 'user': owner}
        with patch.object(governance, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'admin capability'): governance.apply(policy, value)
            request.assert_not_called()
        value['repository']['permissions']['admin'] = True; value['user'] = {'login': 'other', 'id': 99}
        with patch.object(governance, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'recovery owner'): governance.apply(policy, value)
            request.assert_not_called()

    def test_candidate_has_no_publication_permission_or_commands(self):
        workflow = (ROOT / '.github/workflows/candidate-rehearsal.yml').read_text()
        for forbidden in ['contents: write', 'gh release create', 'git tag']:
            self.assertNotIn(forbidden, workflow)
        self.assertIn('tools/prepare_release.py --candidate', workflow)
        release = (ROOT / '.github/workflows/release.yml').read_text()
        self.assertEqual(release.count('      contents: write'), 1)
        self.assertIn('environment: release', release)

    def test_read_only_authority_and_publication_credentials_are_separate(self):
        candidate = (ROOT / '.github/workflows/candidate-rehearsal.yml').read_text()
        release = (ROOT / '.github/workflows/release.yml').read_text()
        secret = 'GH_AUTHORITY_TOKEN: ${{ secrets.RELEASE_AUTHORITY_READ_TOKEN }}'
        self.assertEqual(candidate.count(secret), 1)
        self.assertNotIn('GH_TOKEN: ${{ github.token }}', candidate)
        self.assertEqual(release.count(secret), 2)
        write = release.split('- name: Publish verified package without authority credentials', 1)[1]
        self.assertNotIn('GH_AUTHORITY_TOKEN', write)
        self.assertIn('GH_TOKEN: ${{ github.token }}', write)
        self.assertNotIn('tools/prepare_release.py', write)
        self.assertNotIn('tools/repository_governance.py', write)
        for document in ['docs/GITHUB_SETUP.md', 'docs/RELEASE_AUTHORITY.md']:
            text = (ROOT / document).read_text()
            self.assertNotIn('annotated', text)
            self.assertIn('lightweight', text)


if __name__ == '__main__':
    unittest.main()
