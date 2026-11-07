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
                  'bench/measurement_attestation.py', 'bench/fixture_health.py')


def payload(folder, candidate):
    environment = loads((folder / 'environment.json').read_text())
    return {'schema_version': 1, 'purpose': 'completed benchmark measurement',
            'candidate': candidate, 'run_id': environment['run_id'],
            'started_at': environment['started_at'], 'finished_at': environment['finished_at'],
            'manifest_sha256': sha256((folder / 'manifest.json').read_bytes()),
            'installation_sha256': sha256((folder / 'installation.json').read_bytes()),
            'protocol_files': {name: sha256((ROOT / name).read_bytes()) for name in PROTOCOL_FILES}}


def attest_measurement(folder, candidate, private_key):
    """Called only after the final artifact inventory has been completed."""
    target = folder / 'measurement.json'
    require(not target.exists(), 'measurement attestation already exists')
    target.write_text(json.dumps(sign(payload(folder, candidate), private_key), indent=2) + '\n')


def verify_measurement(folder, candidate, trusted_key):
    path = folder / 'measurement.json'
    require(path.is_file(), 'signed completed measurement required')
    envelope = loads(path.read_text())
    require(isinstance(envelope, dict) and set(envelope) == {'payload', 'signature'},
            'invalid completed measurement envelope')
    signature = envelope['signature']
    require(isinstance(signature, str) and re.fullmatch('[0-9a-f]{128}', signature),
            'invalid completed measurement signature')
    require(envelope['payload'] == payload(folder, candidate),
            'completed measurement binding differs')
    with tempfile.TemporaryDirectory(prefix='pgjt-measurement-verify-') as temporary:
        source, detached = Path(temporary) / 'payload', Path(temporary) / 'signature'
        source.write_bytes(canonical(envelope['payload']))
        detached.write_bytes(bytes.fromhex(signature))
        result = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-rawin',
            '-inkey', str(trusted_key), '-in', str(source), '-sigfile', str(detached)],
            capture_output=True)
        require(result.returncode == 0, 'completed measurement signature verification failed')
