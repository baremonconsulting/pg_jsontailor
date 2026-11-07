#!/usr/bin/env python3
"""Explicit, checked-in development/candidate/final metadata transitions."""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads

STATES = ('development', 'candidate', 'final')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def validate(value):
    require(set(value) == {'schema_version', 'version', 'state', 'release_date'}, 'invalid release metadata schema')
    require(value['schema_version'] == 1 and value['state'] in STATES, 'invalid metadata version/state')
    require(re.fullmatch(r'\d+\.\d+\.\d+', value['version']), 'invalid product version')
    if value['state'] == 'final':
        require(isinstance(value['release_date'], str), 'final metadata requires a release date')
        date.fromisoformat(value['release_date'])
    else:
        require(value['release_date'] is None, 'unreleased metadata must not have a release date')
    return value


def transition(previous, current):
    validate(previous); validate(current)
    if previous['version'] != current['version']:
        old = tuple(map(int, previous['version'].split('.')))
        new = tuple(map(int, current['version'].split('.')))
        require(new > old and current['state'] == 'development', 'new version must start later in development')
    else:
        delta = STATES.index(current['state']) - STATES.index(previous['state'])
        require(delta in {0, 1}, 'reverse or skipped metadata transition')
        if delta == 0:
            require(previous == current, 'mixed metadata within one state')


def verify(root, previous=None, required=None):
    value = validate(loads((root / 'release-metadata.json').read_text()))
    if previous is None:
        prior = subprocess.run(['git', 'show', 'HEAD^:release-metadata.json'], cwd=root,
                               capture_output=True, text=True)
        if prior.returncode == 0:
            previous = loads(prior.stdout)
    if previous is not None:
        transition(previous, value)
    if required:
        require(value['state'] == required, f'release metadata is not {required}')
    version, state = value['version'], value['state']
    control = (root / 'pg_jsontailor.control').read_text()
    citation = (root / 'CITATION.cff').read_text()
    changelog = (root / 'CHANGELOG.md').read_text()
    readme = (root / 'README.md').read_text()
    require(re.search(r"^default_version = '" + re.escape(version) + "'$", control, re.M), 'control version differs')
    require(re.search(r'^version: ' + re.escape(version) + '$', citation, re.M), 'citation version differs')
    heading = f'## {version} — {value["release_date"] if state == "final" else "unreleased"}'
    require(heading in changelog and len(re.findall(r'^## ' + re.escape(version) + ' —', changelog, re.M)) == 1,
            'dated/unreleased changelog heading differs')
    marker = f'Release metadata: `{version}` / `{state}`.'
    require(marker in readme, 'README metadata state differs')
    require(f'release-state: {state}' in citation, 'CITATION metadata state differs')
    if state == 'final':
        require(f'date-released: {value["release_date"]}' in citation, 'CITATION release date differs')
        require(f'The unreleased {version}' not in readme, 'final README still claims an unreleased version')
        require(f'`{version}` remains unreleased' not in changelog, 'final changelog still claims an unreleased version')
    else:
        require('date-released:' not in citation, 'unreleased citation has a release date')
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--require-state', choices=STATES)
    args = parser.parse_args()
    previous = loads(args.previous.read_text()) if args.previous else None
    verify(args.root, previous, args.require_state)
    print('release metadata: PASS')


if __name__ == '__main__':
    main()
