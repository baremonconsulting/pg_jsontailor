#!/usr/bin/env python3
"""Crash-safe, non-publishable benchmark diagnostics."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
from typing import Any

from bench.installation import canonical, sha256, verify_attestation, require
from bench.verify_results import verify_plan_node, verify_semantics
from bench.workload import SIZES, OPERATIONS, IMPLEMENTATIONS, statements

JOURNAL = ".diagnostic-events.jsonl"
MANIFEST = ".diagnostic-manifest.json"
SCHEMA_VERSION = 1


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".new")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


class DiagnosticJournal:
    """Durably retain the completed prefix without changing benchmark measurements."""

    def __init__(self, folder: Path, provenance: dict[str, Any]):
        self.folder = folder
        self.path = folder / JOURNAL
        self.manifest = folder / MANIFEST
        self.sequence = 0
        self.previous = "0" * 64
        self.journal_hash = hashlib.sha256()
        self.closed = False
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.path.exists() or self.manifest.exists():
            raise RuntimeError("diagnostic evidence already exists")
        self.append("header", provenance)

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        if self.closed:
            raise RuntimeError("diagnostic journal is already terminal")
        body = {"sequence": self.sequence, "previous": self.previous,
                "kind": kind, "payload": payload}
        digest = sha256(canonical(body))
        event = {**body, "digest": digest}
        encoded = canonical(event) + b"\n"
        with self.path.open("ab", buffering=0) as stream:
            if stream.write(encoded) != len(encoded):
                raise OSError("short diagnostic journal write")
            os.fsync(stream.fileno())
        self.journal_hash.update(encoded)
        self.sequence += 1
        self.previous = digest
        self._manifest("INCOMPLETE/UNFINALISED")

    def _manifest(self, status: str) -> None:
        _atomic_json(self.manifest, {
            "schema_version": SCHEMA_VERSION,
            "purpose": "diagnostic-only; never publishable benchmark evidence",
            "status": status,
            "events": self.sequence,
            "last_event": self.previous,
            "journal_sha256": self.journal_hash.hexdigest(),
        })

    def fail(self, phase: str, error: BaseException, restoration: str,
             interrupted: dict[str, Any] | None = None) -> None:
        self.append("terminal-failure", {
            "phase": phase,
            "error_type": type(error).__name__,
            "error": str(error),
            "restoration": restoration,
            "interrupted": interrupted,
        })
        self._manifest("FAIL")
        self.closed = True

    def discard_after_success(self) -> None:
        for path in (self.path, self.manifest):
            path.unlink()
        _sync_directory(self.folder)


def verify_diagnostic(folder: Path, *, candidate: str, trusted_key: Path,
                      repo: Path) -> dict[str, Any]:
    """Classify diagnostic evidence as terminal fail, incomplete prefix or corrupt."""
    folder = folder.resolve()
    manifest_path, journal_path = folder / MANIFEST, folder / JOURNAL
    if not journal_path.is_file():
        raise RuntimeError("diagnostic journal is absent")
    try:
        lines = journal_path.read_bytes().splitlines()
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("corrupt diagnostic journal") from error
    if not events:
        raise RuntimeError("empty diagnostic journal")
    previous = "0" * 64
    for sequence, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != {
                "sequence", "previous", "kind", "payload", "digest"}:
            raise RuntimeError("invalid diagnostic event schema")
        body = {name: event[name] for name in ["sequence", "previous", "kind", "payload"]}
        if event["sequence"] != sequence or event["previous"] != previous:
            raise RuntimeError("diagnostic sequence or chain differs")
        if sha256(canonical(body)) != event["digest"]:
            raise RuntimeError("diagnostic event digest differs")
        previous = event["digest"]
    header = events[0]
    required = {"source_commit", "source_dirty", "installation_sha256", "installation_log_sha256",
                "workload_sha256", "producer_sha256", "command", "started_at"}
    if header["kind"] != "header" or not isinstance(header["payload"], dict) \
            or set(header["payload"]) != required or header["payload"]["source_dirty"] is not False:
        raise RuntimeError("invalid diagnostic provenance")
    provenance = header["payload"]
    require(re.fullmatch('[0-9a-f]{40}', candidate) is not None
            and provenance['source_commit'] == candidate, 'diagnostic candidate differs')
    for name in ['installation_sha256', 'installation_log_sha256', 'workload_sha256',
                 'producer_sha256']:
        require(isinstance(provenance[name], str)
                and re.fullmatch('[0-9a-f]{64}', provenance[name]), 'invalid diagnostic digest')
    installation = folder / 'installation.json'
    installation_log = folder / 'installation.log'
    require(sha256(installation.read_bytes()) == provenance['installation_sha256'],
            'diagnostic installation envelope differs')
    require(sha256(installation_log.read_bytes()) == provenance['installation_log_sha256'],
            'diagnostic installation log differs')
    require(sha256((repo / 'bench/workload.py').read_bytes()) == provenance['workload_sha256']
            and sha256((repo / 'bench/duality_overhead.py').read_bytes()) == provenance['producer_sha256'],
            'diagnostic protocol identity differs')
    verify_attestation(installation, repo=repo, candidate=candidate, trusted_key=trusted_key)

    environment_seen = False
    sessions, states, completed_coordinates, active = set(), {}, set(), None
    statement_plans = []
    last_completed = None
    for event in events[1:]:
        kind, payload = event['kind'], event['payload']
        require(isinstance(payload, dict), 'invalid diagnostic payload')
        if kind == 'environment':
            require(not environment_seen and not sessions and active is None,
                    'diagnostic environment order differs')
            required_environment = {'postgresql', 'postgresql_major', 'server_settings', 'platform',
                'cpu', 'memory', 'storage', 'load', 'database', 'pg_host', 'pg_port', 'pg_user',
                'pg_bin', 'python', 'sizes', 'operations', 'warmups', 'maximum_warmups',
                'stability_threshold', 'repetitions', 'sessions'}
            require(set(payload) == required_environment and payload['sizes'] == list(SIZES)
                    and payload['operations'] == list(OPERATIONS) and payload['warmups'] == 10
                    and payload['maximum_warmups'] == 30 and payload['stability_threshold'] == .10
                    and payload['repetitions'] >= 50 and payload['sessions'] == 3
                    and isinstance(payload['server_settings'], dict) and payload['server_settings'],
                    'invalid diagnostic environment')
            environment_seen = True
        elif kind == 'session-start':
            require(environment_seen and active is None and set(payload) == {'session', 'pid', 'backend_start'}
                    and payload['session'] == len(sessions) and payload['session'] in range(3)
                    and type(payload['pid']) is int and payload['pid'] > 0
                    and isinstance(payload['backend_start'], str) and payload['backend_start'],
                    'invalid diagnostic backend identity/order')
            sessions.add(payload['session'])
        elif kind == 'semantic-state':
            require(active is not None and set(payload) == {'sha256', 'value'}
                    and sha256(canonical(payload['value'])) == payload['sha256'],
                    'invalid diagnostic semantic state')
            states[payload['sha256']] = payload['value']
        elif kind == 'constituent-start':
            require(environment_seen and sessions and active is None, 'overlapping diagnostic constituent')
            required_coordinates = {'session', 'size', 'operation', 'implementation', 'run', 'warmup', 'pair_order'}
            require(set(payload) == required_coordinates and payload['session'] in sessions
                    and payload['size'] in SIZES and payload['operation'] in OPERATIONS
                    and payload['implementation'] in IMPLEMENTATIONS and type(payload['run']) is int
                    and payload['run'] >= 0 and type(payload['warmup']) is bool
                    and payload['pair_order'] in {0, 1}, 'invalid diagnostic constituent coordinates')
            order = IMPLEMENTATIONS if payload['run'] % 2 == 0 else tuple(reversed(IMPLEMENTATIONS))
            require(order[payload['pair_order']] == payload['implementation'], 'diagnostic pair order differs')
            coordinate_key = tuple(payload[name] for name in
                ['session', 'size', 'operation', 'implementation', 'run'])
            require(coordinate_key not in completed_coordinates,
                    'duplicate diagnostic logical attempt')
            active, statement_plans = payload, []
        elif kind == 'statement-complete':
            require(active is not None and all(payload.get(k) == v for k, v in active.items()),
                    'diagnostic statement coordinates differ')
            index = payload.get('statement')
            expected = statements(active['operation'], active['implementation'], active['size'])
            require(type(index) is int and index == len(statement_plans) and index < len(expected)
                    and payload.get('sql') == expected[index] and isinstance(payload.get('plan'), dict),
                    'diagnostic statement inventory differs')
            plan = payload['plan']
            require({'Plan', 'Planning Time', 'Execution Time'} <= set(plan), 'diagnostic plan root differs')
            verify_plan_node(plan['Plan'])
            require(type(plan['Planning Time']) in {int, float} and plan['Planning Time'] >= 0
                    and type(plan['Execution Time']) in {int, float} and plan['Execution Time'] >= 0,
                    'invalid diagnostic plan time')
            statement_plans.append(plan)
        elif kind == 'constituent-complete':
            require(active is not None and all(payload.get(k) == v for k, v in active.items()),
                    'diagnostic completion coordinates differ')
            expected = statements(active['operation'], active['implementation'], active['size'])
            require(len(statement_plans) == len(expected) and payload.get('restoration') == 'verified',
                    'diagnostic constituent incomplete')
            refs = [payload.get(name) for name in ['before', 'after', 'rollback']]
            require(all(ref in states for ref in refs), 'diagnostic semantic reference missing')
            verify_semantics({'before': states[refs[0]], 'after': states[refs[1]],
                              'rollback': states[refs[2]],
                              'branch': active['operation'].removeprefix('merge_')
                              if active['operation'].startswith('merge_') else 'not_merge'},
                             active['size'], active['operation'])
            require(payload.get('execution_ms') == sum(p['Execution Time'] for p in statement_plans)
                    and payload.get('planning_ms') == sum(p['Planning Time'] for p in statement_plans)
                    and payload.get('shared_hit_blocks') == sum(p['Plan'].get('Shared Hit Blocks', 0) for p in statement_plans)
                    and payload.get('shared_dirtied_blocks') == sum(p['Plan'].get('Shared Dirtied Blocks', 0) for p in statement_plans)
                    and payload.get('wal_records') == sum(p['Plan'].get('WAL Records', 0) for p in statement_plans),
                    'diagnostic plan/counter reconciliation differs')
            completed_coordinates.add(tuple(active[name] for name in
                ['session', 'size', 'operation', 'implementation', 'run']))
            last_completed, active = dict(active), None
        elif kind == 'terminal-failure':
            require(event is events[-1] and set(payload) == {'phase', 'error_type', 'error', 'restoration', 'interrupted'}
                    and payload['restoration'] in {'verified', 'failed', 'not-reached'},
                    'invalid diagnostic terminal failure')
            require(payload['phase'] in {'database-setup', 'constituent', 'statement',
                    'semantic-read-back', 'rollback', 'restoration', 'between-arms'},
                    'invalid diagnostic failure phase')
            if payload['restoration'] == 'verified':
                require(active is None and last_completed is not None and payload['phase'] == 'between-arms',
                        'diagnostic restoration is not witnessed')
        else:
            raise RuntimeError('unknown diagnostic event kind')
    if not manifest_path.is_file():
        return {"outcome": "INCOMPLETE_PREFIX", "events": len(events), "publishable": False,
                "restoration_verified": False}
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("corrupt diagnostic manifest") from error
    expected = {"schema_version", "purpose", "status", "events", "last_event", "journal_sha256"}
    if not isinstance(manifest, dict) or set(manifest) != expected or manifest["schema_version"] != 1 \
            or manifest["purpose"] != "diagnostic-only; never publishable benchmark evidence" \
            or type(manifest["events"]) is not int or not 0 < manifest["events"] <= len(events):
        raise RuntimeError("diagnostic manifest binding differs")
    bound_count = manifest["events"]
    bound_bytes = b"\n".join(lines[:bound_count]) + b"\n"
    if manifest["last_event"] != events[bound_count - 1]["digest"] \
            or manifest["journal_sha256"] != sha256(bound_bytes):
        raise RuntimeError("diagnostic manifest binding differs")
    if bound_count < len(events):
        return {"outcome": "INCOMPLETE_PREFIX", "events": len(events), "publishable": False,
                "restoration_verified": False}
    if manifest["status"] == "INCOMPLETE/UNFINALISED":
        return {"outcome": "INCOMPLETE_PREFIX", "events": len(events), "publishable": False,
                "restoration_verified": False}
    if manifest["status"] != "FAIL" or events[-1]["kind"] != "terminal-failure":
        raise RuntimeError("invalid diagnostic terminal state")
    restoration = events[-1]["payload"].get("restoration")
    if restoration not in {"verified", "failed", "not-reached"}:
        raise RuntimeError("invalid restoration result")
    return {"outcome": "TERMINAL_FAIL", "events": len(events), "publishable": False,
            "restoration_verified": restoration == "verified"}
