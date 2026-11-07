"""Signed exact-candidate installation evidence, consumed independently of timing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

from tools.strict_results import loads


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def candidate_files(repo, commit):
    require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit)
            and commit != '0' * 40, 'invalid exact candidate SHA')
    listing = subprocess.check_output(['git', 'ls-tree', '-rz', commit, '--',
                                       'sql', 'pg_jsontailor.control'], cwd=repo)
    files = {}
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t')
        mode, kind, _ = meta.split()
        require(kind == b'blob' and mode in {b'100644', b'100755'}, 'non-regular candidate input')
        path = name.decode()
        files[path] = sha256(subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=repo))
    require('pg_jsontailor.control' in files and any(p.startswith('sql/') for p in files),
            'candidate installation inputs missing')
    return files


def sign(payload, private_key):
    with tempfile.TemporaryDirectory(prefix='pgjt-sign-') as temporary:
        source = Path(temporary) / 'payload'
        source.write_bytes(canonical(payload))
        signature = subprocess.check_output(['openssl', 'pkeyutl', '-sign', '-rawin',
                                             '-inkey', str(private_key), '-in', str(source)])
    return {'payload': payload, 'signature': signature.hex()}


def verify_attestation(path, *, repo, candidate, trusted_key, live=None):
    """The key is supplied by the reviewer/release owner, never taken from evidence."""
    record = loads(Path(path).read_text())
    require(isinstance(record, dict) and set(record) == {'payload', 'signature'},
            'invalid installation signature envelope')
    payload = record['payload']
    fields = {'schema_version', 'candidate', 'signer', 'started_at', 'finished_at',
              'source_files', 'installed_files', 'expected_catalogue_sha256',
              'catalogue_sha256', 'expected_generated_ddl', 'generated_ddl',
              'postgresql', 'pg_config', 'install_prefix', 'commands', 'log_sha256'}
    require(isinstance(payload, dict) and set(payload) == fields and payload['schema_version'] == 1,
            'invalid installation attestation schema')
    require(payload['candidate'] == candidate, 'installed candidate SHA differs')
    require(isinstance(payload['signer'], str) and payload['signer'].strip(), 'installation signer missing')
    from datetime import datetime
    times = [datetime.fromisoformat(payload[key]) for key in ['started_at', 'finished_at']]
    require(all(value.tzinfo is not None for value in times) and times[0] <= times[1],
            'invalid installation timestamps')
    signature = record['signature']
    require(isinstance(signature, str) and re.fullmatch('[0-9a-f]{128}', signature),
            'invalid Ed25519 signature')
    with tempfile.TemporaryDirectory(prefix='pgjt-verify-') as temporary:
        source, detached = Path(temporary) / 'payload', Path(temporary) / 'signature'
        source.write_bytes(canonical(payload)); detached.write_bytes(bytes.fromhex(signature))
        result = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-rawin',
                                 '-inkey', str(trusted_key), '-in', str(source),
                                 '-sigfile', str(detached)], capture_output=True)
        require(result.returncode == 0, 'installation signature verification failed')
    expected = candidate_files(repo, candidate)
    require(payload['source_files'] == expected and payload['installed_files'] == expected,
            'installed control/SQL differs from exact candidate')
    require(re.fullmatch('[0-9a-f]{64}', payload['catalogue_sha256'])
            and payload['expected_catalogue_sha256'] == payload['catalogue_sha256'],
            'installed catalogue generator differs')
    require(isinstance(payload['generated_ddl'], dict)
            and set(payload['generated_ddl']) == {'root', 'object_array'}
            and all(re.fullmatch('[0-9a-f]{64}', value) for value in payload['generated_ddl'].values())
            and payload['expected_generated_ddl'] == payload['generated_ddl'],
            'representative generated DDL differs')
    require(isinstance(payload['commands'], list) and len(payload['commands']) >= 4
            and all(isinstance(c, dict) and set(c) == {'argv', 'started_at', 'elapsed_seconds', 'returncode'}
                    and isinstance(c['argv'], list) and c['argv']
                    and all(isinstance(a, str) for a in c['argv'])
                    and type(c['returncode']) is int and c['returncode'] == 0
                    and type(c['elapsed_seconds']) in {int, float} and c['elapsed_seconds'] >= 0
                    for c in payload['commands']), 'installation command trace missing/failed')
    require(re.fullmatch('[0-9a-f]{64}', payload['log_sha256']), 'installation log digest missing')
    require(sha256(path.with_name('installation.log').read_bytes()) == payload['log_sha256'],
            'installation log digest differs')
    require(isinstance(payload['postgresql'], str) and payload['postgresql']
            and all(isinstance(payload[k], str) and Path(payload[k]).is_absolute()
                    for k in ['pg_config', 'install_prefix']), 'installation environment missing')
    if live is not None:
        observed = live()
        require(observed == {'installed_files': payload['installed_files'],
                             'catalogue_sha256': payload['catalogue_sha256'],
                             'generated_ddl': payload['generated_ddl']},
                'live installation differs from signed candidate')
    return payload
