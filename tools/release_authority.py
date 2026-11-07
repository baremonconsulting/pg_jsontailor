#!/usr/bin/env python3
"""Fail-closed workflow authority, separate from candidate validation.

Live collection uses complete, counted REST pages. Pure verification also accepts
trusted test snapshots; fixture success is not live release approval.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads
from tools.evidence_index import verify as verify_index, check_file

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / '.github/release-policy.json'


def require(value, message):
    if not value:
        raise RuntimeError(message)


class GitHub:
    def __init__(self, repository):
        require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository), 'invalid repository')
        self.repository = repository

    def get(self, path):
        result = subprocess.run(['gh', 'api', '-H', 'Accept: application/vnd.github+json',
                                 '-H', 'X-GitHub-Api-Version: 2022-11-28',
                                 f'repos/{self.repository}/{path}'], text=True, capture_output=True)
        require(result.returncode == 0, f'GitHub API failure: {path}: {result.stderr.strip()}')
        return loads(result.stdout)

    def pages(self, path, field):
        rows, total = [], None
        for page in range(1, 1001):
            data = self.get(f'{path}{"&" if "?" in path else "?"}per_page=100&page={page}')
            count, batch = data.get('total_count'), data.get(field)
            require(type(count) is int and count >= 0 and isinstance(batch, list), 'invalid counted API page')
            require(total is None or count == total, 'API count changed during pagination')
            total = count
            rows.extend(batch)
            require(len(rows) <= total, 'API pagination exceeds count')
            if len(rows) == total:
                require(len({row['id'] for row in rows}) == total, 'duplicate paginated API identities')
                return rows
            require(len(batch) == 100, 'incomplete API pagination')
        raise RuntimeError('API pagination limit exceeded')


def collect(api, policy, sha, mode, ref):
    names = ['nightly'] if mode == 'nightly' else (['ci'] if mode == 'candidate' else ['ci', 'codeql', 'dependency'])
    snapshot = {'repository': api.repository, 'workflows': {}}
    if mode == 'main':
        snapshot['branch'] = api.get('branches/main')
        snapshot['protection'] = api.get('branches/main/protection')
        snapshot['comparison'] = api.get(f'compare/{sha}...main')
    checks = api.pages(f'commits/{sha}/check-runs?filter=all', 'check_runs')
    for name in names:
        expected = policy['workflows'][name]
        workflow = api.get(f'actions/workflows/{expected["id"]}')
        runs = api.pages(f'actions/workflows/{expected["id"]}/runs?head_sha={sha}', 'workflow_runs')
        eligible = [run for run in runs if run.get('head_sha') == sha
                    and run.get('head_branch') == ref.removeprefix('refs/heads/')
                    and run.get('event') in ({'push'} if mode == 'main' else {'schedule', 'workflow_dispatch'})]
        require(eligible, f'{name}: no authoritative {mode} run for exact SHA/ref')
        latest = max(eligible, key=lambda run: run['id'])
        run = api.get(f'actions/runs/{latest["id"]}')
        attempt = run['run_attempt']
        jobs = api.pages(f'actions/runs/{run["id"]}/attempts/{attempt}/jobs', 'jobs')
        suite = api.get(f'check-suites/{run["check_suite_id"]}')
        after = api.get(f'actions/runs/{run["id"]}')
        keys = ['id', 'run_attempt', 'status', 'conclusion', 'head_sha', 'head_branch', 'event', 'check_suite_id']
        require(all(after[key] == run[key] for key in keys), 'run changed while collecting its attempt')
        refreshed = api.pages(f'actions/workflows/{expected["id"]}/runs?head_sha={sha}', 'workflow_runs')
        def fingerprint(values):
            return sorted(tuple(item[key] for key in keys) for item in values)
        require(fingerprint(refreshed) == fingerprint(runs), 'workflow runs changed during authority collection')
        snapshot['workflows'][name] = dict(workflow=workflow, runs=runs, run=run, jobs=jobs,
                                            checks=checks, suite=suite)
    return snapshot


def verify(snapshot, policy, sha, mode='main', ref='refs/heads/main'):
    require(re.fullmatch('[0-9a-f]{40}', sha) and sha != '0' * 40, 'invalid exact SHA')
    require(mode in {'main', 'nightly', 'candidate'}, 'unknown authority policy')
    require(snapshot['repository'] == policy['repository'], 'repository identity differs')
    require(ref.startswith('refs/heads/'), 'authority requires a branch ref')
    if mode == 'main':
        require(ref == 'refs/heads/main', 'release authority requires protected main')
        require(snapshot['branch']['name'] == 'main' and snapshot['branch']['protected'] is True,
                'main is not protected')
        require(snapshot['protection']['enforce_admins']['enabled'] is True,
                'main admin enforcement is absent')
        comparison = snapshot['comparison']
        require(comparison['status'] in {'identical', 'ahead'} and comparison['base_commit']['sha'] == sha
                and comparison['merge_base_commit']['sha'] == sha
                and (comparison['commits'][-1]['sha'] if comparison['commits'] else sha)
                == snapshot['branch']['commit']['sha'], 'SHA is not an ancestor of protected main')
    names = {'nightly'} if mode == 'nightly' else ({'ci'} if mode == 'candidate' else {'ci', 'codeql', 'dependency'})
    require(set(snapshot['workflows']) == names, 'authority workflow inventory differs')
    identities = {}
    for name in names:
        expected, evidence = policy['workflows'][name], snapshot['workflows'][name]
        workflow, runs, run = evidence['workflow'], evidence['runs'], evidence['run']
        require(workflow['id'] == expected['id'] and workflow['path'] == expected['path']
                and workflow['state'] == 'active', f'{name}: workflow identity differs')
        require(len({r['id'] for r in runs}) == len(runs), f'{name}: duplicate run identities')
        require(all(r['head_sha'] == sha and r['workflow_id'] == expected['id'] for r in runs),
                f'{name}: conflicting run query')
        eligible = [r for r in runs if r['head_branch'] == ref.removeprefix('refs/heads/')
                    and r['event'] in ({'push'} if mode == 'main' else {'schedule', 'workflow_dispatch'})]
        require(eligible, f'{name}: no authoritative {mode} run for exact SHA/ref')
        latest = max(eligible, key=lambda r: r['id'])
        for key in ['id', 'run_attempt', 'workflow_id', 'head_sha', 'head_branch', 'event',
                    'status', 'conclusion', 'check_suite_id', 'path']:
            require(run[key] == latest[key], f'{name}: incoherent latest run/attempt: {key}')
        require(run['workflow_id'] == expected['id'] and run['path'] == expected['path']
                and run['status'] == 'completed' and run['conclusion'] == 'success',
                f'{name}: latest authoritative run did not succeed')
        require(type(run['run_attempt']) is int and run['run_attempt'] > 0, 'invalid run attempt')
        suite = evidence['suite']
        require(suite['id'] == run['check_suite_id'] and suite['head_sha'] == sha
                and suite['app']['id'] == policy['actions_app_id'], f'{name}: untrusted Actions suite')
        jobs, checks = evidence['jobs'], evidence['checks']
        require(len({job['id'] for job in jobs}) == len(jobs), 'duplicate job identities')
        require(len(jobs) == len(expected['jobs']) and {j['name'] for j in jobs} == set(expected['jobs']),
                f'{name}: complete job inventory differs')
        require(len({check['id'] for check in checks}) == len(checks), 'duplicate check identities')
        for job in jobs:
            require(job['run_id'] == run['id'] and job['run_attempt'] == run['run_attempt']
                    and job['head_sha'] == sha and job['status'] == 'completed'
                    and job['conclusion'] == 'success', f'{name}: stale/unsuccessful job')
            match = re.fullmatch(r'https://api\.github\.com/repos/' + re.escape(policy['repository'])
                                 + r'/check-runs/([0-9]+)', job['check_run_url'])
            require(match is not None, 'job check URL has an untrusted repository')
            matches = [c for c in checks if c['id'] == int(match.group(1))]
            require(len(matches) == 1, f'{name}: missing job check run')
            check = matches[0]
            require(check['name'] == job['name'] and check['head_sha'] == sha
                    and check['check_suite']['id'] == suite['id']
                    and check['app']['id'] == policy['actions_app_id']
                    and check['status'] == 'completed' and check['conclusion'] == 'success',
                    f'{name}: untrusted or conflicting check run')
        identities[name] = {'sha': sha, 'workflow_id': expected['id'], 'workflow_path': expected['path'],
                            'event': run['event'], 'ref': ref, 'run_id': run['id'], 'attempt': run['run_attempt']}
    return identities


def verify_evidence(index_path, sha, root, approval, identities):
    index = verify_index(index_path, sha, root, approval)
    for entry in index['bundles']:
        bundle = loads(check_file(root, entry).read_text())
        require(bundle['run'] == identities[bundle['kind']], 'index is not bound to authoritative run/attempt')
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sha', required=True)
    parser.add_argument('--mode', choices=['main', 'nightly'], default='main')
    parser.add_argument('--ref', default='refs/heads/main')
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    args = parser.parse_args()
    policy = loads(POLICY.read_text())
    api = GitHub(policy['repository'])
    snapshot = loads(args.snapshot.read_text()) if args.snapshot else collect(api, policy, args.sha, args.mode, args.ref)
    identities = verify(snapshot, policy, args.sha, args.mode, args.ref)
    # Both policies always consume the approved, complete index. Neither accepts a naked green check.
    if args.mode == 'main':
        nightly = collect(api, policy, args.sha, 'nightly', args.ref)
        identities.update(verify(nightly, policy, args.sha, 'nightly', args.ref))
        verify_evidence(args.index, args.sha, args.root, args.approval, identities)
    else:
        index = verify_index(args.index, args.sha, args.root, args.approval)
        for entry in index['bundles']:
            bundle = loads(check_file(args.root, entry).read_text())
            if bundle['kind'] == 'nightly':
                require(bundle['run'] == identities['nightly'], 'nightly index identity differs')
    print(f'{args.mode} exact-SHA authority: PASS')


if __name__ == '__main__':
    main()
