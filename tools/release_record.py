#!/usr/bin/env python3
"""Canonical signed rehearsal/publication records with pinned reviewer keys.

Public keys are provisioned in a reviewed commit before freeze. Private keys
never enter the source tree or evidence store. An empty trust store denies all.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads
from tools.evidence_index import digest


def require(value, message):
    if not value:
        raise RuntimeError(message)


def canonical(record):
    return (json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def validate(record):
    require(set(record) == {'schema_version', 'kind', 'commit', 'version', 'evidence_index_sha256',
                            'archive_sha256', 'approvals', 'signer', 'withdrawal_owner',
                            'created_at', 'release_approval'}, 'invalid release record schema')
    require(record['schema_version'] == 1 and record['kind'] in {'candidate', 'publication'}, 'invalid record kind')
    require(record['release_approval'] is (record['kind'] == 'publication'), 'candidate is not release approval')
    require(re.fullmatch('[0-9a-f]{40}', record['commit']) and record['commit'] != '0' * 40, 'invalid record SHA')
    require(re.fullmatch(r'\d+\.\d+\.\d+', record['version']), 'invalid record version')
    for key in ['evidence_index_sha256', 'archive_sha256']:
        require(re.fullmatch('[0-9a-f]{64}', record[key]), 'invalid record digest')
    for key in ['signer', 'withdrawal_owner']:
        require(isinstance(record[key], str) and record[key].strip(), f'missing named {key}')
    def timestamp(value):
        require(isinstance(value, str), 'missing approval timestamp')
        require(datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None,
                'record timestamps require timezones')
    timestamp(record['created_at'])
    require(isinstance(record['approvals'], list) and record['approvals'], 'missing record approvals')
    identities = set()
    for approval in record['approvals']:
        require(set(approval) == {'reviewer', 'role', 'approved_at'}, 'invalid record approval schema')
        require(isinstance(approval['reviewer'], str) and approval['reviewer'].strip(), 'missing approval identity')
        require(approval['reviewer'] not in identities, 'duplicate approval identity')
        identities.add(approval['reviewer']); timestamp(approval['approved_at'])
        require(approval['role'] in {'evidence-reviewer', 'release-reviewer'}, 'invalid approval role')
    require(any(a['role'] == 'evidence-reviewer' for a in record['approvals']), 'missing evidence approval')
    if record['kind'] == 'publication':
        require(any(a['role'] == 'release-reviewer' and a['reviewer'] != record['signer']
                    for a in record['approvals']), 'missing independent release approval')
    return record


def crypt(command, record):
    with tempfile.TemporaryDirectory(prefix='pgjt-signature-') as temporary:
        payload = Path(temporary) / 'record.json'
        payload.write_bytes(canonical(record))
        result = subprocess.run(['openssl', 'dgst', '-sha256', *command, str(payload)],
                                capture_output=True)
        require(result.returncode == 0, 'release record signature failed')


def verify(record_path, signature, public_key, trust, sha, index, archive, kind):
    record = validate(loads(record_path.read_text()))
    require(trust['schema_version'] == 1 and isinstance(trust['signers'], list), 'invalid signer trust store')
    matches = [entry for entry in trust['signers'] if entry['identity'] == record['signer']
               and entry['public_key_sha256'] == digest(public_key) and kind in entry['record_kinds']]
    require(len(matches) == 1, 'record signer/key is not independently pinned')
    crypt(['-verify', str(public_key), '-signature', str(signature)], record)
    require(record['commit'] == sha and record['kind'] == kind, 'signed record SHA/kind differs')
    require(record['evidence_index_sha256'] == digest(index) and record['archive_sha256'] == digest(archive),
            'signed record index/archive differs')
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['sign', 'verify'])
    parser.add_argument('record', type=Path)
    parser.add_argument('--signature', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--trust', type=Path, default=Path(__file__).resolve().parents[1] / '.github/release-signers.json')
    parser.add_argument('--sha')
    parser.add_argument('--index', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--kind', choices=['candidate', 'publication'])
    args = parser.parse_args()
    if args.command == 'sign':
        require(not args.signature.exists(), 'never overwrite a signature')
        crypt(['-sign', str(args.key), '-out', str(args.signature)], validate(loads(args.record.read_text())))
    else:
        require(all([args.sha, args.index, args.archive, args.kind]), 'verification requires SHA, index, archive and kind')
        verify(args.record, args.signature, args.key, loads(args.trust.read_text()),
               args.sha, args.index, args.archive, args.kind)
    print('signed release record: PASS')


if __name__ == '__main__':
    main()
