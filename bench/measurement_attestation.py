"""Authenticate a completed benchmark, not merely its installation."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile

from bench.installation import canonical, require, sha256, sign
from tools.strict_results import loads

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_FILES = ('bench/duality_overhead.py', 'bench/workload.py',
                  'bench/benchmark_execution.py', 'bench/verify_results.py',
                  'bench/measurement_attestation.py', 'bench/fixture_health.py',
                  'bench/batching_protocol.py', 'bench/diagnostic_evidence.py')


def candidate_protocol(repo, candidate, name):
    content = subprocess.run(['git', '-C', str(repo), 'show', f'{candidate}:{name}'],
                             capture_output=True)
    require(content.returncode == 0, 'measurement candidate protocol file is unavailable: ' + name)
    return sha256(content.stdout)


def payload(folder, candidate, repo=ROOT):
    environment = loads((folder / 'environment.json').read_text())
    return {'schema_version': 1, 'purpose': 'completed benchmark measurement',
            'candidate': candidate, 'run_id': environment['run_id'],
            'started_at': environment['started_at'], 'finished_at': environment['finished_at'],
            'manifest_sha256': sha256((folder / 'manifest.json').read_bytes()),
            'installation_sha256': sha256((folder / 'installation.json').read_bytes()),
            'protocol_files': {name: candidate_protocol(repo, candidate, name)
                               for name in PROTOCOL_FILES}}


def attest_measurement(folder, candidate, private_key, repo=ROOT):
    """Called only after the final artifact inventory has been completed."""
    target = folder / 'measurement.json'
    require(not target.exists(), 'measurement attestation already exists')
    expected = payload(folder, candidate, repo)
    require(all(sha256((repo / name).read_bytes()) == digest
                for name, digest in expected['protocol_files'].items()),
            'measurement working protocol differs from candidate')
    target.write_text(json.dumps(sign(expected, private_key), indent=2) + '\n')


def verify_measurement(folder, candidate, trusted_key, repo=ROOT):
    path = folder / 'measurement.json'
    require(path.is_file(), 'signed completed measurement required')
    envelope = loads(path.read_text())
    require(isinstance(envelope, dict) and set(envelope) == {'payload', 'signature'},
            'invalid completed measurement envelope')
    signature = envelope['signature']
    require(isinstance(signature, str) and re.fullmatch('[0-9a-f]{128}', signature),
            'invalid completed measurement signature')
    require(envelope['payload'] == payload(folder, candidate, repo),
            'completed measurement binding differs')
    with tempfile.TemporaryDirectory(prefix='pgjt-measurement-verify-') as temporary:
        source, detached = Path(temporary) / 'payload', Path(temporary) / 'signature'
        source.write_bytes(canonical(envelope['payload']))
        detached.write_bytes(bytes.fromhex(signature))
        result = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-rawin',
            '-inkey', str(trusted_key), '-in', str(source), '-sigfile', str(detached)],
            capture_output=True)
        require(result.returncode == 0, 'completed measurement signature verification failed')
