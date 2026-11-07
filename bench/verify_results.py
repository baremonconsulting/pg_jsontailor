#!/usr/bin/env python3
"""Fail closed on installation, reviewed workload, semantics, plans and sampling."""
from __future__ import annotations
import argparse
import collections
import csv
import json
import math
from pathlib import Path
import re
import statistics
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads
from bench.installation import canonical, require, sha256, verify_attestation
from bench.workload import SIZES, OPERATIONS, IMPLEMENTATIONS, statements

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = {'shared_hit_blocks': 'Shared Hit Blocks',
            'shared_dirtied_blocks': 'Shared Dirtied Blocks', 'wal_records': 'WAL Records'}
ARTIFACTS = {'measurements.csv', 'plans.json', 'summary.json', 'environment.json',
             'semantics.json', 'installation.json', 'installation.log'}
ENVIRONMENT_FIELDS = {'schema_version', 'run_id', 'source_commit', 'source_dirty', 'sizes',
    'operations', 'warmups', 'maximum_warmups', 'stability_threshold', 'repetitions', 'sessions',
    'session_backends', 'postgresql', 'postgresql_major', 'server_settings', 'platform', 'cpu',
    'memory', 'storage', 'load', 'database', 'pg_host', 'pg_port', 'pg_user', 'pg_bin', 'command',
    'started_at', 'finished_at', 'python', 'direct_contract', 'restart_policy'}
FIELDS = ['session', 'size', 'operation', 'implementation', 'run', 'warmup', 'pair_order',
          'execution_ms', 'planning_ms', *COUNTERS, 'status']


def number(value, message):
    require(type(value) in {int, float} and math.isfinite(value) and value >= 0, message)
    return value


def statistics_for(values):
    values = sorted(values)
    return {'median_ms': statistics.median(values), 'p95_ms': values[math.ceil(.95 * len(values)) - 1],
            'min_ms': min(values), 'max_ms': max(values)}


def stable(values):
    previous, recent = statistics.median(values[-10:-5]), statistics.median(values[-5:])
    return previous > 0 and abs(recent - previous) / previous <= .10


def expected_state(size, operation='read'):
    def document(identity, status, qty):
        return {'_id': identity, 'status': status, 'created': '2026-11-03',
                'customer': {'customerId': 1, 'name': 'Benchmark customer'},
                'items': [{'itemId': identity * 10000 + item, 'product': f'Product {item}',
                           'qty': qty, 'price': 10.25} for item in range(1, size + 1)]}
    source, inserted = document(1000 + size, 'new', 1), None
    if operation in {'update', 'merge_update'}:
        source = document(1000 + size, 'updated', 2)
    elif operation == 'root_update':
        source = document(1000 + size, 'root-updated', 1)
    elif operation in {'delete', 'merge_delete'}:
        source = None
    elif operation in {'insert', 'merge_insert'}:
        inserted = document(100000 + size, 'inserted', 2)
    return {'relational': {'source': source, 'inserted': inserted},
            'documents': {'source': source, 'inserted': inserted}}


def verify_semantics(observed, size, operation):
    require(isinstance(observed, dict) and set(observed) == {'before', 'after', 'rollback', 'branch'},
            'invalid semantic evidence schema')
    require(observed['before'] == expected_state(size), 'semantic precondition/cardinality differs')
    require(observed['after'] == expected_state(size, operation), 'semantic resulting state differs')
    require(observed['rollback'] == expected_state(size), 'semantic rollback restoration differs')
    branch = operation.removeprefix('merge_') if operation.startswith('merge_') else 'not_merge'
    require(observed['branch'] == branch, 'semantic MERGE branch differs')


def verify_plan_node(node):
    allowed = {'Result', 'ProjectSet', 'ModifyTable', 'Append', 'Merge Append', 'Recursive Union',
        'BitmapAnd', 'BitmapOr', 'Nested Loop', 'Merge Join', 'Hash Join', 'Seq Scan', 'Sample Scan',
        'Gather', 'Gather Merge', 'Index Scan', 'Index Only Scan', 'Bitmap Index Scan', 'Bitmap Heap Scan',
        'Tid Scan', 'Tid Range Scan', 'Subquery Scan', 'Function Scan', 'Table Function Scan', 'Values Scan',
        'CTE Scan', 'Named Tuplestore Scan', 'WorkTable Scan', 'Foreign Scan', 'Custom Scan', 'Materialize',
        'Memoize', 'Sort', 'Incremental Sort', 'Group', 'Aggregate', 'WindowAgg', 'Unique', 'SetOp',
        'LockRows', 'Limit', 'Hash'}
    require(isinstance(node, dict) and node.get('Node Type') in allowed, 'invalid/missing plan node')
    for key in ['Actual Rows', 'Actual Loops']:
        require(key in node, 'plan lacks actual execution evidence')
        number(node[key], 'invalid actual execution counter')
    for key, value in node.items():
        if key.endswith(' Blocks') or key.startswith('WAL ') or key.startswith('Tuples '):
            require(type(value) is int and value >= 0, 'non-integer plan counter')
    children = node.get('Plans', [])
    require(isinstance(children, list), 'invalid nested plan inventory')
    for child in children:
        verify_plan_node(child)


def verify_environment(environment, installation, candidate):
    require(isinstance(environment, dict) and set(environment) == ENVIRONMENT_FIELDS
            and environment['schema_version'] == 3, 'benchmark environment schema differs')
    require(environment['sizes'] == list(SIZES) and all(type(v) is int for v in environment['sizes'])
            and environment['operations'] == list(OPERATIONS), 'reviewed workload differs (shrink forbidden)')
    require(environment['source_dirty'] is False and candidate is not None
            and environment['source_commit'] == candidate, 'benchmark requires exact immutable candidate')
    require(environment['postgresql'] == installation['postgresql'], 'installed/benchmark PostgreSQL differs')
    require(environment['postgresql_major'] in {'17', '18', '19'}
            and environment['postgresql'].startswith(environment['postgresql_major']), 'unsupported PostgreSQL major')
    require(type(environment['warmups']) is int and environment['warmups'] == 10
            and environment['maximum_warmups'] == 30 and environment['stability_threshold'] == .10
            and type(environment['repetitions']) is int and environment['repetitions'] >= 50
            and type(environment['sessions']) is int and environment['sessions'] == 3, 'sampling protocol differs')
    backends = environment['session_backends']
    require(isinstance(backends, list) and len(backends) == 3
            and all(isinstance(b, dict) and set(b) == {'session', 'pid', 'backend_start'}
                    and type(b['pid']) is int and b['pid'] > 0 and b['session'] == index
                    and isinstance(b['backend_start'], str) and b['backend_start']
                    for index, b in enumerate(backends))
            and len({(b['pid'], b['backend_start']) for b in backends}) == 3, 'sessions not independently restarted')
    for name in ['run_id', 'platform', 'cpu', 'memory', 'storage', 'load', 'database', 'pg_host',
                 'pg_port', 'pg_user', 'pg_bin', 'python', 'started_at', 'finished_at']:
        require(isinstance(environment[name], str) and environment[name].strip(), 'missing environment: ' + name)
    from datetime import datetime
    dates = [datetime.fromisoformat(environment[name]) for name in ['started_at', 'finished_at']]
    require(all(d.tzinfo is not None for d in dates) and dates[0] < dates[1], 'invalid benchmark timestamps')
    require(isinstance(environment['command'], list) and environment['command'], 'benchmark invocation missing')
    require(environment['restart_policy'] == 'new persistent backend per session'
            and environment['direct_contract'] == 'lower-contract direct SQL', 'comparison/session label differs')
    settings = environment['server_settings']
    require(isinstance(settings, dict) and {'jit', 'max_parallel_workers_per_gather', 'shared_buffers',
            'work_mem', 'synchronous_commit', 'fsync', 'full_page_writes', 'server_version_num',
            'max_connections', 'effective_cache_size', 'checkpoint_timeout', 'wal_level',
            'default_transaction_isolation', 'TimeZone', 'lc_collate', 'lc_ctype'} <= set(settings)
            and settings['jit'] == 'off' and settings['max_parallel_workers_per_gather'] == '0'
            and all(isinstance(v, str) and v for v in settings.values()), 'server settings incomplete/differ')


def verify_results(folder: Path, *, candidate=None, trusted_key=None, repo=ROOT):
    folder = folder.resolve()
    manifest = loads((folder / 'manifest.json').read_text())
    require(isinstance(manifest, dict) and set(manifest) == {'schema_version', 'files'}
            and manifest['schema_version'] == 1 and isinstance(manifest['files'], dict)
            and set(manifest['files']) == ARTIFACTS, 'benchmark manifest inventory differs')
    for name, digest in manifest['files'].items():
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest)
                and sha256((folder / name).read_bytes()) == digest, 'benchmark manifest digest differs: ' + name)
    require(trusted_key is not None, 'independently trusted installation key required')
    installation = verify_attestation(folder / 'installation.json', repo=repo, candidate=candidate,
                                      trusted_key=trusted_key)
    environment = loads((folder / 'environment.json').read_text())
    verify_environment(environment, installation, candidate)
    summaries, plans, semantics = [loads((folder / name).read_text())
                                  for name in ['summary.json', 'plans.json', 'semantics.json']]
    require(isinstance(plans, dict) and isinstance(semantics, dict) and isinstance(summaries, list),
            'invalid benchmark artifact roots')
    require(set(semantics) == {'states', 'samples'} and isinstance(semantics['states'], dict)
            and isinstance(semantics['samples'], dict), 'invalid semantic content store')
    states, semantics = semantics['states'], semantics['samples']
    require(all(sha256(canonical(value)) == digest for digest, value in states.items()), 'semantic state digest differs')
    used_states, semantic_checks = set(), set()
    with (folder / 'measurements.csv').open(newline='') as source:
        reader = csv.DictReader(source)
        require(reader.fieldnames == FIELDS, 'measurement field inventory differs/duplicate')
        rows = list(reader)
    groups, pairs, keys = collections.defaultdict(list), collections.defaultdict(list), set()
    for row in rows:
        require(None not in row and all(v is not None for v in row.values()), 'malformed CSV row')
        for field in ['session', 'size', 'run', 'pair_order', *COUNTERS]:
            require(re.fullmatch('0|[1-9][0-9]*', row[field]) is not None, 'non-integer measurement counter: ' + field)
        session, size, run = int(row['session']), int(row['size']), int(row['run'])
        operation, impl = row['operation'], row['implementation']
        require(session in range(3) and size in SIZES and operation in OPERATIONS
                and impl in IMPLEMENTATIONS, 'unexpected measurement group')
        require(row['status'] == 'PASS' and row['warmup'] in {'True', 'False'}, 'non-PASS/noncanonical measurement')
        order = IMPLEMENTATIONS if run % 2 == 0 else tuple(reversed(IMPLEMENTATIONS))
        require(int(row['pair_order']) == order.index(impl), 'pair order does not alternate')
        pairs[session, size, operation, run].append(impl)
        key = f'{session}:{size}:{operation}:{impl}:{run}'
        require(key not in keys, 'duplicate measurement')
        keys.add(key)
        require(key in plans and key in semantics, 'sample lacks plan/semantic evidence')
        evidence = semantics[key]
        require(isinstance(evidence, dict) and set(evidence) == {'before', 'after', 'rollback', 'branch'}, 'invalid semantic sample')
        require(all(evidence[name] in states for name in ['before', 'after', 'rollback']), 'missing semantic state')
        used_states.update(evidence[name] for name in ['before', 'after', 'rollback'])
        semantic_key = size, operation, evidence['before'], evidence['after'], evidence['rollback'], evidence['branch']
        if semantic_key not in semantic_checks:
            verify_semantics({**{name: states[evidence[name]] for name in ['before', 'after', 'rollback']},
                              'branch': evidence['branch']}, size, operation)
            semantic_checks.add(semantic_key)
        for name in ['execution_ms', 'planning_ms']:
            number(float(row[name]), 'invalid measurement duration')
        captured = plans[key]
        require(isinstance(captured, dict) and set(captured) == {'statements', 'plans'}
                and captured['statements'] == statements(operation, impl, size)
                and isinstance(captured['plans'], list)
                and len(captured['plans']) == len(captured['statements']), 'statement/plan inventory differs')
        for plan in captured['plans']:
            require(isinstance(plan, dict) and {'Plan', 'Planning Time', 'Execution Time'} <= set(plan), 'invalid/missing plan root')
            verify_plan_node(plan['Plan'])
            for name in ['Planning Time', 'Execution Time']:
                number(plan[name], 'invalid plan duration')
        for column, field in [('execution_ms', 'Execution Time'), ('planning_ms', 'Planning Time')]:
            require(math.isclose(float(row[column]), sum(p[field] for p in captured['plans']),
                                 rel_tol=1e-12, abs_tol=1e-12), 'plan duration reconciliation differs')
        for column, field in COUNTERS.items():
            require(int(row[column]) == sum(p['Plan'].get(field, 0) for p in captured['plans']), 'plan integer counter reconciliation differs')
        if operation.startswith('merge_'):
            action = {'merge_insert': 'Inserted', 'merge_update': 'Updated', 'merge_delete': 'Deleted'}[operation]
            for index, plan in enumerate(captured['plans']):
                root = plan['Plan']
                count = 1 if impl == 'duality' or index == (1 if operation == 'merge_delete' else 0) else size
                require(root['Node Type'] == 'ModifyTable' and root.get('Operation') == 'Merge', 'MERGE plan root/branch differs')
                require(type(root.get('Tuples ' + action)) is int and root['Tuples ' + action] == count, 'MERGE affected cardinality differs')
        groups[session, size, operation, impl].append(row)
    require(set(plans) == keys and set(semantics) == keys, 'extra/missing plan/semantic samples')
    require(set(states) == used_states, 'unreferenced semantic state')
    require(set(groups) == {(s, n, op, impl) for s in range(3) for n in SIZES for op in OPERATIONS for impl in IMPLEMENTATIONS}, 'reviewed workload groups incomplete')
    for (*_, run), positions in pairs.items():
        require(tuple(positions) == (IMPLEMENTATIONS if run % 2 == 0 else tuple(reversed(IMPLEMENTATIONS))), 'paired samples reordered/incomplete')
    warmup_counts = {}
    for key, samples in groups.items():
        require([int(r['run']) for r in samples] == list(range(len(samples))), 'sample run sequence differs')
        warmups, measured = [[r for r in samples if r['warmup'] == flag] for flag in ['True', 'False']]
        count = len(warmups)
        require(10 <= count <= 30 and count % 5 == 0 and len(measured) == environment['repetitions']
                and samples == warmups + measured, 'warm-up/measured sample structure differs')
        require(stable([float(r['execution_ms']) for r in warmups]), 'warm-up stability gate failed')
        warmup_counts[key] = count
    for session in range(3):
        for size in SIZES:
            for operation in OPERATIONS:
                counts = [warmup_counts[session, size, operation, impl] for impl in IMPLEMENTATIONS]
                require(counts[0] == counts[1], 'paired warm-up counts differ')
                for earlier in range(10, counts[0], 5):
                    require(not all(stable([float(r['execution_ms']) for r in groups[session, size, operation, impl][:earlier]]) for impl in IMPLEMENTATIONS), 'unnecessary extra warm-ups')
    expected_summaries = {(s, n, op) for s in range(3) for n in SIZES for op in OPERATIONS}
    seen = set()
    for summary in summaries:
        require(isinstance(summary, dict) and set(summary) == {'session', 'size', 'operation', 'direct', 'duality', 'median_ratio'}, 'invalid summary schema')
        key = summary['session'], summary['size'], summary['operation']
        require(type(key[0]) is int and type(key[1]) is int and key in expected_summaries and key not in seen, 'duplicate/unexpected summary')
        seen.add(key)
        for impl in IMPLEMENTATIONS:
            calculated = statistics_for([float(r['execution_ms']) for r in groups[key + (impl,)] if r['warmup'] == 'False'])
            require(isinstance(summary[impl], dict) and set(summary[impl]) == set(calculated), 'summary statistics differ')
            require(all(type(summary[impl][field]) in {int, float} and math.isclose(summary[impl][field], value, rel_tol=1e-12, abs_tol=1e-12) for field, value in calculated.items()), 'summary differs from nearest-rank/raw statistics')
        require(summary['direct']['median_ms'] > 0 and type(summary['median_ratio']) in {int, float}
                and math.isclose(summary['median_ratio'], summary['duality']['median_ms'] / summary['direct']['median_ms'], rel_tol=1e-12, abs_tol=1e-12), 'summary ratio differs')
    require(seen == expected_summaries, 'summary inventory differs')
    return {'status': 'PASS', 'measurements': len(rows), 'groups': len(summaries), 'candidate': candidate}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('folder', type=Path)
    parser.add_argument('--candidate', required=True)
    parser.add_argument('--trusted-key', required=True, type=Path)
    parser.add_argument('--repo', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(verify_results(args.folder, candidate=args.candidate, trusted_key=args.trusted_key, repo=args.repo), indent=2))


if __name__ == '__main__':
    main()
