#!/usr/bin/env python3
"""Shared nonpublishing source package, evidence, signature and authority path."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.evidence_index import verify as verify_index, check_file, digest
from tools.release_authority import GitHub, collect, verify, verify_evidence, require, POLICY
from tools.release_metadata import verify as verify_metadata
from tools.release_record import verify as verify_record
from tools.strict_results import loads
from tools.verify_generated_sql_notice import verify_archive

ROOT = Path(__file__).resolve().parents[1]


def prepare(sha, ref, evidence, output, candidate):
    require(not output.exists(), 'never overwrite an existing package directory')
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == sha,
            'checkout is not the exact candidate SHA')
    metadata = verify_metadata(ROOT, required=None if candidate else 'final')
    if candidate:
        # Final source metadata may be rehearsed on a candidate branch before
        # freeze. It never grants protected-main publication authority.
        require(ref != 'refs/heads/main' and ref.startswith('refs/heads/'), 'rehearsal requires a candidate branch')
    else:
        require(ref == 'refs/heads/main', 'publication preparation requires main')
    index_path, approval = evidence / 'evidence-index.json', evidence / 'approval.json'
    index = verify_index(index_path, sha, evidence, approval)
    archive_source = check_file(evidence, index['archive'])
    version = metadata['version']
    output.mkdir(parents=True)
    archive = output / f'pg_jsontailor-{version}.tar.gz'
    subprocess.run(['git', 'archive', '--format=tar.gz', f'--prefix=pg_jsontailor-{version}/',
                    '-o', str(archive), sha], cwd=ROOT, check=True)
    require(digest(archive) == digest(archive_source), 'rebuilt source archive differs from approved index')
    verify_archive(archive)
    kind = 'candidate' if candidate else 'publication'
    record = verify_record(evidence / 'record.json', evidence / 'record.sig', evidence / 'signer.pem',
                           loads((ROOT / '.github/release-signers.json').read_text()), sha,
                           index_path, archive, kind)
    require(record['version'] == version, 'signed record version differs')
    trust = loads((ROOT / '.github/release-signers.json').read_text())
    benchmark_key = trust.get('benchmark_key')
    require(isinstance(benchmark_key, dict) and set(benchmark_key) == {'path', 'sha256'},
            'independently provisioned benchmark trust key is missing')
    key = (ROOT / benchmark_key['path']).resolve()
    require(key.is_relative_to((ROOT / '.github').resolve()) and key.is_file()
            and digest(key) == benchmark_key['sha256'], 'benchmark key is outside protected source trust')
    from tools.verify_release_candidate import verify_benchmark_matrix
    verify_benchmark_matrix([evidence / 'benchmark' / f'pg{major}' for major in ['17', '18', '19']], sha, key, ROOT)
    approved = loads(approval.read_text())
    require(any(item['reviewer'] == approved['approver'] and item['role'] == 'evidence-reviewer'
                for item in record['approvals']), 'signed approval does not match evidence approver')
    policy = loads(POLICY.read_text()); api = GitHub(policy['repository'])
    nightly = verify(collect(api, policy, sha, 'nightly', ref), policy, sha, 'nightly', ref)
    mode = 'candidate' if candidate else 'main'
    authority = verify(collect(api, policy, sha, mode, ref), policy, sha, mode, ref)
    verify_evidence(index_path, sha, evidence, approval, {**authority, **nightly})
    if candidate:
        try:
            verify(collect(api, policy, sha, 'main', 'refs/heads/main'), policy, sha)
        except RuntimeError as error:
            require('no authoritative main run for exact SHA/ref' in str(error)
                    or 'SHA is not an ancestor of protected main' in str(error),
                    f'release gate failed for an unexpected reason: {error}')
        else:
            raise RuntimeError('candidate unexpectedly satisfied protected-main release authority')
    (output / f'{archive.name}.sha256').write_text(f'{digest(archive)}  {archive.name}\n')
    for name in ['record.json', 'record.sig']:
        (output / name).write_bytes((evidence / name).read_bytes())
    print(f'{kind} package verification: PASS; ' + ('NOT RELEASE APPROVAL' if candidate else 'publication preparation only'))
    return archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--ref', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidate', action='store_true')
    args = parser.parse_args()
    prepare(args.sha, args.ref, args.evidence, args.output, args.candidate)


if __name__ == '__main__':
    main()
