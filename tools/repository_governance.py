#!/usr/bin/env python3
"""Read back governance; opt-in, owner-checked settings changes only.

No branches, tags or releases are written. Do not guess release reviewers: the
protected release environment requires a separately named independent reviewer.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.release_authority import GitHub, require
from tools.strict_results import loads

ROOT = Path(__file__).resolve().parents[1]


def request(path, method='GET', payload=None):
    command = ['gh', 'api', '-X', method, '-H', 'X-GitHub-Api-Version: 2022-11-28', path]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True)
    require(result.returncode == 0, f'governance API failed: {method} {path}: {result.stderr.strip()}')
    return loads(result.stdout) if result.stdout.strip() else None


def read(policy):
    base = f'repos/{policy["repository"]}'
    repo, user = request(base), request('user')
    actions = request(base + '/actions/permissions')
    rules = request(base + '/rulesets?per_page=100')
    require(isinstance(rules, list) and len(rules) < 100, 'ruleset pagination requires manual investigation')
    return {'repository': repo, 'user': user,
            'protection': request(base + '/branches/main/protection'),
            'rulesets': [request(base + f'/rulesets/{rule["id"]}') for rule in rules],
            'actions': actions,
            'selected_actions': request(base + '/actions/permissions/selected-actions')
                                if actions['allowed_actions'] == 'selected' else None,
            'private_reporting': request(base + '/private-vulnerability-reporting'),
            'immutable_releases': request(base + '/immutable-releases')}


def owner_preflight(snapshot, policy):
    repo, user = snapshot['repository'], snapshot['user']
    owner = policy['emergency_owner']
    require(repo['full_name'] == policy['repository'] and repo['permissions']['admin'] is True,
            'authenticated account does not have read-back admin capability')
    require(repo['owner']['type'] == 'User' and repo['owner']['login'] == owner['login']
            and repo['owner']['id'] == owner['id'] and user['id'] == owner['id']
            and user['login'] == owner['login'], 'emergency recovery owner has not been verified')
    protection = snapshot['protection']
    require(protection['required_pull_request_reviews']['required_approving_review_count'] == 0,
            'do not lock out a sole owner with unavailable independent merge approval')
    require(protection['allow_force_pushes']['enabled'] is False
            and protection['allow_deletions']['enabled'] is False, 'unsafe pre-existing main policy')


def findings(snapshot, policy):
    failures = []
    if not snapshot['protection']['enforce_admins']['enabled']:
        failures.append('main admin enforcement missing')
    tags = [r for r in snapshot['rulesets'] if r['name'] == policy['tag_ruleset_name']]
    if len(tags) != 1:
        failures.append('version tag ruleset missing/duplicated')
    else:
        rule = tags[0]
        if not (rule['target'] == 'tag' and rule['enforcement'] == 'active' and not rule['bypass_actors']
                and rule['conditions']['ref_name'] == {'include': policy['tag_patterns'], 'exclude': []}
                and {r['type'] for r in rule['rules']} == {'update', 'deletion'}):
            failures.append('version tag immutability differs')
    actions, selected = snapshot['actions'], snapshot['selected_actions']
    if not (actions['enabled'] is True and actions['allowed_actions'] == 'selected'
            and actions.get('sha_pinning_required') is True and selected
            and selected['github_owned_allowed'] is False and selected['verified_allowed'] is False
            and set(selected['patterns_allowed']) == set(policy['actions'])):
        failures.append('SHA-pinned action allow-list differs')
    if snapshot['private_reporting']['enabled'] is not True:
        failures.append('private vulnerability reporting disabled')
    if snapshot['immutable_releases']['enabled'] is not True:
        failures.append('release immutability disabled')
    return failures


def apply(policy, before):
    owner_preflight(before, policy)
    # A second complete read immediately before writing detects intervening changes.
    fresh = read(policy)
    require(fresh == before, 'governance changed after preflight; inspect before retrying')
    base = f'repos/{policy["repository"]}'
    rules = [r for r in before['rulesets'] if r['name'] == policy['tag_ruleset_name']]
    require(len(rules) <= 1, 'ambiguous existing tag ruleset')
    desired = {'name': policy['tag_ruleset_name'], 'target': 'tag', 'enforcement': 'active',
               'bypass_actors': [], 'conditions': {'ref_name': {'include': policy['tag_patterns'], 'exclude': []}},
               'rules': [{'type': 'update'}, {'type': 'deletion'}]}
    if not rules:
        request(base + '/rulesets', 'POST', desired)
    else:
        # Never automatically replace an existing policy; it may be stronger.
        require(not any('tag' in finding for finding in findings(before, policy)),
                'existing tag policy differs; reviewed migration required')
    request(base + '/branches/main/protection/enforce_admins', 'POST')
    request(base + '/actions/permissions', 'PUT',
            {'enabled': True, 'allowed_actions': 'selected', 'sha_pinning_required': True})
    request(base + '/actions/permissions/selected-actions', 'PUT',
            {'github_owned_allowed': False, 'verified_allowed': False, 'patterns_allowed': policy['actions']})
    request(base + '/private-vulnerability-reporting', 'PUT')
    request(base + '/immutable-releases', 'PUT')
    after = read(policy)
    require(not findings(after, policy), 'post-change governance read-back failed')
    return after


def verify_environment(value, owner_id):
    require(value['name'] == 'release' and value.get('can_admins_bypass') is False,
            'release environment absent or permits admin bypass')
    reviewers = [rule for rule in value['protection_rules'] if rule['type'] == 'required_reviewers']
    require(len(reviewers) == 1 and reviewers[0].get('prevent_self_review') is True,
            'release environment must prohibit self-review')
    require(any(r['reviewer']['id'] != owner_id for r in reviewers[0]['reviewers']),
            'independent release reviewer missing')
    require(value['deployment_branch_policy'] == {'protected_branches': True, 'custom_branch_policies': False},
            'release environment refs are not restricted to protected branches')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--check-release-environment', action='store_true')
    args = parser.parse_args()
    policy = loads((ROOT / '.github/governance-policy.json').read_text())
    if args.check_release_environment:
        require(not args.apply, 'environment check is read-only')
        value = request(f'repos/{policy["repository"]}/environments/release')
        verify_environment(value, policy['emergency_owner']['id'])
        require(request(f'repos/{policy["repository"]}/immutable-releases')['enabled'] is True,
                'immutable releases are not enabled')
        print('live release environment: PASS')
        return
    require(args.evidence is not None, 'governance capture requires an evidence path')
    require(not args.evidence.exists(), 'never overwrite governance evidence')
    before = read(policy)
    owner_preflight(before, policy)
    evidence = {'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
                'policy_sha256': hashlib.sha256((ROOT / '.github/governance-policy.json').read_bytes()).hexdigest(),
                'before': before, 'before_findings': findings(before, policy), 'applied': False}
    # Persist preflight even if a later write fails. Evidence belongs outside the freeze commit.
    args.evidence.write_text(json.dumps(evidence, sort_keys=True, indent=2) + '\n')
    if args.apply:
        evidence['after'] = apply(policy, before)
        evidence['applied'] = True
        evidence['after_findings'] = findings(evidence['after'], policy)
        args.evidence.write_text(json.dumps(evidence, sort_keys=True, indent=2) + '\n')
    require(not findings(evidence.get('after', before), policy), 'governance findings remain; see captured evidence')
    print('live repository governance: PASS')


if __name__ == '__main__':
    main()
