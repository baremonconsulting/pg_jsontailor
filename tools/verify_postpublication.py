#!/usr/bin/env python3
"""Verify immutable tag identity and downloaded release assets after publication."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evidence_index import digest
from tools.release_authority import GitHub, require
from tools.release_record import verify as verify_record
from tools.strict_results import loads
from tools.verify_generated_sql_notice import verify_archive


def verify(snapshot, sha, version, archive, checksum, record, signature, key, trust, index):
    signed = verify_record(record, signature, key, trust, sha, index, archive, 'publication')
    require(signed['version'] == version, 'published version differs from signed record')
    release, tag = snapshot['release'], snapshot['tag']
    expected_tag = f'v{version}'
    require(tag['ref'] == f'refs/tags/{expected_tag}' and tag['object']['type'] == 'commit'
            and tag['object']['sha'] == sha, 'immutable lightweight tag identity differs')
    require(release['tag_name'] == expected_tag and release['draft'] is False and release['prerelease'] is False,
            'release is missing, draft or prerelease')
    require(release.get('immutable') is True, 'published release is not immutable')
    assets = release['assets']
    expected = {archive.name, checksum.name, record.name, signature.name}
    require(len(assets) == len(expected) and {asset['name'] for asset in assets} == expected,
            'release asset inventory differs')
    for path in [archive, checksum, record, signature]:
        asset = next(a for a in assets if a['name'] == path.name)
        require(asset['state'] == 'uploaded' and asset['size'] == path.stat().st_size
                and asset['digest'] == f'sha256:{digest(path)}', 'published asset digest/state differs')
    require(checksum.read_text() == f'{digest(archive)}  {archive.name}\n', 'published checksum differs')
    verify_archive(archive)
    return signed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--version', required=True)
    for name in ['archive', 'checksum', 'record', 'signature', 'key', 'trust', 'index']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--repository', default='baremonconsulting/pg_jsontailor')
    args = parser.parse_args()
    api = GitHub(args.repository, authority=False)
    snapshot = {'release': api.get(f'releases/tags/v{args.version}'),
                'tag': api.get(f'git/ref/tags/v{args.version}')}
    verify(snapshot, args.sha, args.version, args.archive, args.checksum, args.record, args.signature,
           args.key, loads(args.trust.read_text()), args.index)
    print('post-publication verification: PASS (live API and downloaded assets)')


if __name__ == '__main__':
    main()
