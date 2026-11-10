"""Collector redaction and encrypted diagnostics fail closed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.collect_evidence import collect
from tools.evidence_index import NIGHTLY_FILES
from tools.strict_results import inventories


class CollectorEvidence(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'input'; self.source.mkdir()
        self.output = self.root / 'store/nightly-pg18'
        self.sentinel = 'PRIVATE-DIAGNOSTIC-SENTINEL'
        for name, suite in NIGHTLY_FILES.items():
            rows = [{'id': ident, 'status': 'PASS', 'seed': 50050, 'cases': 24,
                     'operator_matches': 1, 'mutant_compiled': True, 'mutant_killed': True,
                     'detail': self.sentinel, 'observed': {'private': self.sentinel}}
                    for ident in inventories()[suite]]
            (self.source / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
        for name in ['installation.log', 'postgresql.log']:
            (self.source / name).write_text(self.sentinel)
        self.environment = {'GITHUB_SHA': 'a' * 40, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                            'GITHUB_REF': 'refs/heads/candidate', 'GITHUB_RUN_ID': '2',
                            'GITHUB_RUN_ATTEMPT': '1', 'PGBIN': '/nonexistent-test-prefix',
                            'EVIDENCE_DIAGNOSTICS_CERTIFICATE': ''}

    def test_missing_certificate_retains_redacted_partial_evidence_without_plaintext(self):
        with mock.patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(RuntimeError, 'certificate is not configured'):
                collect('nightly', '18', self.source, self.root, self.output, 1)
        self.assertTrue((self.output / 'bundle.json').is_file())
        self.assertFalse(list((self.output / 'diagnostics').iterdir()))
        self.assertNotIn(self.sentinel, ''.join(path.read_text() for path in self.output.rglob('*') if path.is_file()))

    def test_live_encryption_and_decryption_preserve_private_diagnostic(self):
        key, certificate = self.root / 'fixture.key', self.root / 'fixture.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-subj', '/CN=Evidence Fixture', '-days', '1', '-keyout', str(key),
                        '-out', str(certificate)], check=True, capture_output=True, timeout=15)
        self.environment['EVIDENCE_DIAGNOSTICS_CERTIFICATE'] = certificate.read_text()
        with mock.patch.dict(os.environ, self.environment):
            collect('nightly', '18', self.source, self.root, self.output, 1)
        for path in (self.output / 'public').iterdir():
            self.assertNotIn(self.sentinel.encode(), path.read_bytes())
        for name in ['installation.log.p7m', 'postgresql.log.p7m']:
            decrypted = subprocess.run(['openssl', 'cms', '-decrypt', '-binary', '-inform', 'DER',
                '-in', str(self.output / 'diagnostics' / name), '-recip', str(certificate), '-inkey', str(key)],
                check=True, capture_output=True, timeout=15)
            self.assertEqual(decrypted.stdout.decode(), self.sentinel)


if __name__ == '__main__':
    unittest.main()
