"""Adversarial fixtures prove the current-only benchmark algorithms, not live timings."""
from __future__ import annotations
import copy
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.installation import canonical, candidate_files, sha256, sign, verify_attestation
from bench.measurement_attestation import attest_measurement
from bench.verify_results import ARTIFACTS, ENVIRONMENT_FIELDS, FIELDS, expected_state, statistics_for, verify_results
from bench.workload import SIZES, OPERATIONS, IMPLEMENTATIONS, statements
from tools.verify_release_candidate import verify_benchmark_candidate


def write_json(path, value):
    path.write_text(json.dumps(value))


def rehash(folder):
    write_json(folder / 'manifest.json', {'schema_version': 1,
        'files': {name: sha256((folder / name).read_bytes()) for name in sorted(ARTIFACTS)}})


def fixture(folder, repo, candidate, private):
    folder.mkdir()
    moment = datetime.now(timezone.utc).isoformat()
    files = candidate_files(repo, candidate)
    (folder / 'installation.log').write_text('Algorithm fixture only; no live installation or measurement.\n')
    payload = {'schema_version': 1, 'candidate': candidate, 'signer': 'fixture reviewer',
        'started_at': moment, 'finished_at': moment, 'source_files': files, 'installed_files': files,
        'catalogue_sha256': 'c' * 64, 'expected_catalogue_sha256': 'c' * 64,
        'generated_ddl': {'root': 'd' * 64, 'object_array': 'e' * 64},
        'expected_generated_ddl': {'root': 'd' * 64, 'object_array': 'e' * 64},
        'postgresql': '18.6 fixture', 'pg_config': '/fixture/bin/pg_config', 'install_prefix': '/fixture',
        'commands': [{'argv': ['fixture', str(n)], 'started_at': moment,
                      'elapsed_seconds': 0.01, 'returncode': 0} for n in range(4)],
        'log_sha256': sha256((folder / 'installation.log').read_bytes())}
    write_json(folder / 'installation.json', sign(payload, private))
    environment = {name: 'fixture' for name in ENVIRONMENT_FIELDS}
    environment.update(schema_version=3, source_commit=candidate, source_dirty=False,
        sizes=list(SIZES), operations=list(OPERATIONS), warmups=10, maximum_warmups=30,
        stability_threshold=.1, repetitions=50, sessions=3, postgresql='18.6 fixture', postgresql_major='18',
        session_backends=[{'session': n, 'pid': 100 + n, 'backend_start': moment} for n in range(3)],
        started_at='2026-11-07T01:00:00+00:00', finished_at='2026-11-07T01:01:00+00:00',
        command=['fixture-only'], direct_contract='lower-contract direct SQL',
        restart_policy='new persistent backend per session',
        server_settings={name: 'fixture' for name in ['jit', 'max_parallel_workers_per_gather', 'shared_buffers',
            'work_mem', 'synchronous_commit', 'fsync', 'full_page_writes', 'server_version_num',
            'max_connections', 'effective_cache_size', 'checkpoint_timeout', 'wal_level',
            'default_transaction_isolation', 'TimeZone', 'lc_collate', 'lc_ctype']})
    environment['server_settings'].update(jit='off', max_parallel_workers_per_gather='0')
    write_json(folder / 'environment.json', environment)
    rows, plans, states, semantics, summary = [], {}, {}, {}, []
    def intern(value):
        digest = sha256(canonical(value)); states[digest] = value; return digest
    for session in range(3):
        for size in SIZES:
            for operation in OPERATIONS:
                before, after = intern(expected_state(size)), intern(expected_state(size, operation))
                for run in range(60):
                    order = IMPLEMENTATIONS if run % 2 == 0 else tuple(reversed(IMPLEMENTATIONS))
                    for position, impl in enumerate(order):
                        workload = statements(operation, impl, size)
                        execution = 1.0 if run < 10 else 1 + (run - 10) / 100
                        row = {'session': session, 'size': size, 'operation': operation, 'implementation': impl,
                            'run': run, 'warmup': run < 10, 'pair_order': position, 'execution_ms': execution,
                            'planning_ms': len(workload) * .01, 'shared_hit_blocks': 0,
                            'shared_dirtied_blocks': 0, 'wal_records': 0, 'status': 'PASS'}
                        rows.append(row)
                        key = f'{session}:{size}:{operation}:{impl}:{run}'
                        captured = []
                        for index, _ in enumerate(workload):
                            node = {'Node Type': 'Result', 'Actual Rows': 1, 'Actual Loops': 1,
                                    'Shared Hit Blocks': 0, 'Shared Dirtied Blocks': 0, 'WAL Records': 0}
                            if operation.startswith('merge_'):
                                action = {'merge_insert': 'Inserted', 'merge_update': 'Updated', 'merge_delete': 'Deleted'}[operation]
                                count = 1 if impl == 'duality' or index == (1 if operation == 'merge_delete' else 0) else size
                                node.update({'Node Type': 'ModifyTable', 'Operation': 'Merge', 'Tuples ' + action: count})
                            captured.append({'Plan': node, 'Planning Time': .01, 'Execution Time': execution / len(workload)})
                        plans[key] = {'statements': workload, 'plans': captured}
                        semantics[key] = {'before': before, 'after': after, 'rollback': before,
                            'branch': operation.removeprefix('merge_') if operation.startswith('merge_') else 'not_merge'}
                stats = statistics_for([1 + n / 100 for n in range(50)])
                summary.append({'session': session, 'size': size, 'operation': operation,
                                'direct': stats, 'duality': stats, 'median_ratio': 1.0})
    with (folder / 'measurements.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS); writer.writeheader(); writer.writerows(rows)
    write_json(folder / 'plans.json', plans)
    write_json(folder / 'semantics.json', {'states': states, 'samples': semantics})
    write_json(folder / 'summary.json', summary)
    rehash(folder)
    attest_measurement(folder, candidate, private)


class BenchmarkTrust(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='pgjt-benchmark-trust-')
        cls.root = Path(cls.temporary.name)
        cls.repo = cls.root / 'repo'; cls.repo.mkdir(); (cls.repo / 'sql').mkdir()
        (cls.repo / 'pg_jsontailor.control').write_text("default_version = '0.2.0'\n")
        (cls.repo / 'sql/pg_jsontailor--0.2.0.sql').write_text('-- fixture only\n')
        for cmd in [['init', '-q'], ['add', '.'], ['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture']]:
            subprocess.run(['git', *cmd], cwd=cls.repo, check=True)
        cls.candidate = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=cls.repo, text=True).strip()
        cls.private, cls.public = cls.root / 'private.pem', cls.root / 'public.pem'
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(cls.private)], check=True, capture_output=True)
        subprocess.run(['openssl', 'pkey', '-in', str(cls.private), '-pubout', '-out', str(cls.public)], check=True, capture_output=True)
        cls.base = cls.root / 'base'; fixture(cls.base, cls.repo, cls.candidate, cls.private)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.folder = self.root / self.id().split('.')[-1]
        shutil.copytree(self.base, self.folder)
        self.addCleanup(shutil.rmtree, self.folder)

    def verify(self, consumer=verify_results, **kwargs):
        return consumer(self.folder, candidate=self.candidate, trusted_key=self.public, repo=self.repo, **kwargs)

    def mutate_json(self, name, change):
        value = json.loads((self.folder / name).read_text()); change(value)
        write_json(self.folder / name, value); rehash(self.folder)

    def rejected(self, change, message=None):
        change()
        for consumer in [verify_results, verify_benchmark_candidate]:
            with self.subTest(consumer=consumer.__name__), self.assertRaisesRegex(RuntimeError, message or '.'):
                self.verify(consumer)

    def test_valid_complete_current_fixture_both_consumers(self):
        for consumer in [verify_results, verify_benchmark_candidate]:
            result = self.verify(consumer)
            self.assertEqual(result['measurements'], 14400)
        self.assertEqual(statistics_for(list(range(50)))['p95_ms'], 47)

    def test_pgjt_93_unsigned_completed_measurement_rejected(self):
        self.rejected(lambda: (self.folder / 'measurement.json').unlink(),
                      'signed completed measurement required')

    def test_pgjt_93_completed_signature_tamper_rejected(self):
        value = json.loads((self.folder / 'measurement.json').read_text())
        value['signature'] = '0' * 128
        write_json(self.folder / 'measurement.json', value)
        for consumer in (verify_results, verify_benchmark_candidate):
            with self.subTest(consumer=consumer.__name__), self.assertRaisesRegex(
                    RuntimeError, 'completed measurement signature verification failed'):
                self.verify(consumer)

    def test_pgjt_93_coherent_timing_rewrite_rejected(self):
        def rewrite():
            with (self.folder / 'measurements.csv').open(newline='') as source:
                rows = list(csv.DictReader(source))
            for row in rows:
                if row['implementation'] == 'duality':
                    row['execution_ms'] = str(float(row['execution_ms']) * .01)
            with (self.folder / 'measurements.csv').open('w', newline='') as target:
                writer = csv.DictWriter(target, fieldnames=FIELDS)
                writer.writeheader(); writer.writerows(rows)
            plans = json.loads((self.folder / 'plans.json').read_text())
            for key, entry in plans.items():
                if key.split(':')[3] == 'duality':
                    for plan in entry['plans']:
                        plan['Execution Time'] *= .01
            write_json(self.folder / 'plans.json', plans)
            summaries = json.loads((self.folder / 'summary.json').read_text())
            for summary in summaries:
                summary['duality'] = {key: value * .01 for key, value in summary['duality'].items()}
                summary['median_ratio'] = .01
            write_json(self.folder / 'summary.json', summaries)
            rehash(self.folder)
        self.rejected(rewrite, 'completed measurement binding differs')

    def test_self_consistent_workload_shrink_is_rejected(self):
        def shrink():
            self.mutate_json('environment.json', lambda v: v.update(sizes=[0]))
            self.mutate_json('plans.json', lambda v: [v.pop(k) for k in list(v) if k.split(':')[1] != '0'])
            self.mutate_json('semantics.json', lambda v: [v['samples'].pop(k) for k in list(v['samples']) if k.split(':')[1] != '0'])
            self.mutate_json('summary.json', lambda v: v.__setitem__(slice(None), [r for r in v if r['size'] == 0]))
            self.csv_mutation(lambda rows: rows.__setitem__(slice(None), [r for r in rows if r['size'] == '0']))
        self.rejected(shrink, 'reviewed workload differs')

    def csv_mutation(self, change):
        with (self.folder / 'measurements.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        change(rows)
        with (self.folder / 'measurements.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS); writer.writeheader(); writer.writerows(rows)
        rehash(self.folder)

    def test_fractional_integer_counter(self):
        self.rejected(lambda: self.csv_mutation(lambda r: r[0].update(wal_records='0.5')), 'non-integer')

    def test_invalid_plan_root(self):
        self.rejected(lambda: self.mutate_json('plans.json', lambda v: next(iter(v.values()))['plans'][0].update(Plan={})), 'plan node')

    def test_planning_reconciliation(self):
        self.rejected(lambda: self.mutate_json('plans.json', lambda v: next(iter(v.values()))['plans'][0].update({'Planning Time': .5})), 'reconciliation')

    def test_statement_inventory(self):
        self.rejected(lambda: self.mutate_json('plans.json', lambda v: next(iter(v.values()))['statements'].append('SELECT 1')), 'inventory')

    def test_semantic_mutation_even_when_rehashed(self):
        self.rejected(lambda: self.mutate_json('semantics.json', lambda v: next(iter(v['samples'].values())).update(branch='insert')), 'MERGE branch')

    def test_rollback_restoration(self):
        def change(v):
            sample = next(r for r in v['samples'].values() if r['before'] != r['after'])
            sample['rollback'] = sample['after']
        self.rejected(lambda: self.mutate_json('semantics.json', change), 'rollback restoration')

    def test_pair_order(self):
        self.rejected(lambda: self.csv_mutation(lambda r: r[0].update(pair_order='1')), 'pair order')

    def test_reused_session(self):
        self.rejected(lambda: self.mutate_json('environment.json', lambda v: v['session_backends'][1].update(pid=100)), 'restarted')

    def test_wrong_percentile(self):
        self.rejected(lambda: self.mutate_json('summary.json', lambda v: v[0]['direct'].update(p95_ms=1.46)), 'nearest-rank')

    def test_signature_tamper(self):
        self.rejected(lambda: self.mutate_json('installation.json', lambda v: v['payload'].update(signer='altered')), 'signature')

    def test_stale_installation_even_with_valid_signature(self):
        def stale():
            value = json.loads((self.folder / 'installation.json').read_text())['payload']
            value['installed_files'] = {k: 'f' * 64 for k in value['installed_files']}
            write_json(self.folder / 'installation.json', sign(value, self.private)); rehash(self.folder)
        self.rejected(stale, 'installed control/SQL')

    def test_altered_generator_even_with_valid_signature(self):
        def altered():
            value = json.loads((self.folder / 'installation.json').read_text())['payload']
            value['catalogue_sha256'] = 'f' * 64
            write_json(self.folder / 'installation.json', sign(value, self.private)); rehash(self.folder)
        self.rejected(altered, 'catalogue generator')

    def test_live_installation_independently_rechecked(self):
        with self.assertRaisesRegex(RuntimeError, 'live installation differs'):
            verify_attestation(self.folder / 'installation.json', repo=self.repo, candidate=self.candidate,
                               trusted_key=self.public, live=lambda: {})

    def test_duplicate_json_key(self):
        def change():
            path = self.folder / 'environment.json'
            path.write_text(path.read_text().replace('"schema_version": 3', '"schema_version": 3, "schema_version": 3'))
            rehash(self.folder)
        self.rejected(change, 'duplicate JSON key')


if __name__ == '__main__':
    unittest.main()
