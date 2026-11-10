#!/usr/bin/env python3
"""Adversarial checks for failure-safe, non-publishable diagnostics."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.diagnostic_evidence import DiagnosticJournal, MANIFEST, JOURNAL, _candidate_blob, verify_diagnostic
from bench.verify_results import expected_state, verify_results
from bench.verify_results import stable
from bench.installation import canonical, sha256
from bench.benchmark_execution import explain
from bench.batching_protocol import (BATCH_SIZES, CALIBRATION_BATCHES, PROTOCOL_VERSION,
    TARGET_BATCH_MS, arm_order)
from bench.workload import statements

ROOT = Path(__file__).resolve().parents[1]

def provenance(folder: Path) -> dict:
    (folder / "installation.json").write_text("{}\n")
    (folder / "installation.log").write_text("test installation log\n")
    return {"source_commit": "1" * 40, "source_dirty": False,
            "installation_sha256": sha256((folder / "installation.json").read_bytes()),
            "installation_log_sha256": sha256((folder / "installation.log").read_bytes()),
            "workload_sha256": sha256((ROOT / "bench/workload.py").read_bytes()),
            "producer_sha256": sha256((ROOT / "bench/duality_overhead.py").read_bytes()),
            "executor_sha256": sha256((ROOT / "bench/benchmark_execution.py").read_bytes()),
            "batching_sha256": sha256((ROOT / "bench/batching_protocol.py").read_bytes()),
            "diagnostic_verifier_sha256": sha256((ROOT / "bench/diagnostic_evidence.py").read_bytes()),
            "command": ["python3", "bench/duality_overhead.py"],
            "started_at": "2026-11-07T00:00:00+00:00", "semantic_check_only": False}

def verify(folder: Path):
    def candidate_blob(repo, candidate, path):
        return (ROOT / path).read_bytes()
    with mock.patch("bench.diagnostic_evidence.verify_attestation", return_value={'postgresql': '18.6'}), \
            mock.patch("bench.diagnostic_evidence._candidate_blob", side_effect=candidate_blob):
        return verify_diagnostic(folder, candidate="1" * 40,
                                 trusted_key=folder / "trusted.pem", repo=ROOT)

def environment() -> dict:
    settings = {name: 'test' for name in ['shared_buffers', 'work_mem', 'synchronous_commit',
        'fsync', 'full_page_writes', 'server_version_num', 'max_connections',
        'effective_cache_size', 'checkpoint_timeout', 'wal_level',
        'default_transaction_isolation', 'TimeZone', 'lc_collate', 'lc_ctype']}
    settings.update(jit='off', max_parallel_workers_per_gather='0')
    return {'postgresql': '18.6', 'postgresql_major': '18', 'server_settings': settings,
            'platform': 'test', 'cpu': 'test', 'memory': 'test', 'storage': 'test', 'load': 'test',
            'database': 'test', 'pg_host': 'test', 'pg_port': '1', 'pg_user': 'test',
            'pg_bin': '/test', 'python': sys.version, 'sizes': [0, 1, 10, 100, 1000],
            'operations': ['read', 'insert', 'root_update', 'update', 'delete', 'merge_insert',
                           'merge_update', 'merge_delete'], 'warmups': 10, 'maximum_warmups': 30,
            'stability_threshold': .10, 'repetitions': 50, 'sessions': 3,
            'batching_protocol_version': PROTOCOL_VERSION, 'batch_sizes': list(BATCH_SIZES),
            'calibration_batches': CALIBRATION_BATCHES, 'target_batch_ms': TARGET_BATCH_MS,
            'semantic_check_only': False}

def append_constituent(journal, *, phase='calibration', level=1, batch=0,
                       constituent=0, pair_order=0, execution_ms=.001):
    implementation = arm_order(batch, constituent)[pair_order]
    coordinates = {'session': 0, 'size': 0, 'operation': 'read',
        'implementation': implementation, 'phase': phase, 'level': level,
        'batch': batch, 'constituent': constituent, 'batch_size': level,
        'pair_order': pair_order}
    journal.append('constituent-start', coordinates)
    state = expected_state(0)
    digest = sha256(canonical(state))
    journal.append('semantic-state', {'sha256': digest, 'value': state})
    journal.append('semantic-witness', {**coordinates, 'role': 'before', 'sha256': digest})
    workload = statements('read', implementation, 0)
    per_statement = execution_ms / len(workload)
    for index, sql in enumerate(workload):
        plan = {'Plan': {'Node Type': 'Result', 'Actual Rows': 1, 'Actual Loops': 1,
                        'Shared Hit Blocks': 0, 'Shared Dirtied Blocks': 0, 'WAL Records': 0},
                'Planning Time': 0.0, 'Execution Time': per_statement}
        journal.append('statement-complete', {**coordinates, 'statement': index,
                       'sql': sql, 'plan': plan})
    journal.append('semantic-witness', {**coordinates, 'role': 'after', 'sha256': digest})
    journal.append('semantic-witness', {**coordinates, 'role': 'rollback', 'sha256': digest})
    journal.append('constituent-complete', {**coordinates, 'before': digest, 'after': digest,
        'rollback': digest, 'execution_ms': execution_ms, 'planning_ms': 0.0,
        'shared_hit_blocks': 0, 'shared_dirtied_blocks': 0, 'wal_records': 0,
        'restoration': 'verified'})


class DiagnosticTrust(unittest.TestCase):
    def test_pgjt_78_durable_kind_is_visible_before_signal_unmask(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            observed = []
            real_mask = signal.pthread_sigmask
            def inspect_unmask(how, mask):
                if how == signal.SIG_SETMASK:
                    observed.append(journal.last_kind)
                return real_mask(how, mask)
            with mock.patch('bench.diagnostic_evidence.signal.pthread_sigmask',
                            side_effect=inspect_unmask):
                journal.append('environment', environment())
            self.assertEqual(observed, ['environment'])

    def test_pgjt_78_cap_exhaustion_is_a_valid_terminal_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            journal.append('environment', environment())
            journal.append('session-start', {'session': 0, 'pid': 101,
                           'backend_start': '2026-11-07T00:00:01+00:00'})
            for level in BATCH_SIZES:
                totals = {implementation: [] for implementation in ('direct', 'duality')}
                for batch in range(CALIBRATION_BATCHES):
                    for constituent in range(level):
                        for pair_order in range(2):
                            implementation = arm_order(batch, constituent)[pair_order]
                            append_constituent(journal, level=level, batch=batch,
                                               constituent=constituent,
                                               pair_order=pair_order)
                            if len(totals[implementation]) <= batch:
                                totals[implementation].append(0.0)
                            totals[implementation][batch] += .001
                journal.append('calibration-level-complete', {'session': 0, 'size': 0,
                    'operation': 'read', 'level': level, 'totals': totals})
            journal.fail('database-setup', RuntimeError('calibration cap exhausted'),
                         'not-reached', None)
            result = verify(folder)
            self.assertEqual(result['outcome'], 'TERMINAL_FAIL')
            self.assertFalse(result['publishable'])

    def test_pgjt_78_semantic_smoke_failure_is_a_valid_terminal_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            mode = provenance(folder); mode['semantic_check_only'] = True
            journal = DiagnosticJournal(folder, mode)
            smoke_environment = environment(); smoke_environment['semantic_check_only'] = True
            journal.append('environment', smoke_environment)
            journal.append('session-start', {'session': 0, 'pid': 101,
                           'backend_start': '2026-11-07T00:00:01+00:00'})
            append_constituent(journal, phase='measured')
            journal.fail('between-arms', RuntimeError('forced semantic smoke failure'),
                         'verified', None)
            result = verify(folder)
            self.assertEqual(result['outcome'], 'TERMINAL_FAIL')
            self.assertTrue(result['restoration_verified'])

    def test_pgjt_78_aggregate_cannot_reference_another_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            mode = provenance(folder); mode['semantic_check_only'] = True
            journal = DiagnosticJournal(folder, mode)
            smoke_environment = environment(); smoke_environment['semantic_check_only'] = True
            journal.append('environment', smoke_environment)
            journal.append('session-start', {'session': 0, 'pid': 101,
                           'backend_start': '2026-11-07T00:00:01+00:00'})
            append_constituent(journal, phase='measured', pair_order=0)
            append_constituent(journal, phase='measured', pair_order=1)
            journal.append('smoke-group-complete', {'session': 0, 'size': 1,
                'operation': 'read', 'phase': 'measured', 'level': 1, 'batch': 0,
                'totals': {'direct': .001, 'duality': .001}})
            with self.assertRaisesRegex(RuntimeError, 'aggregate group differs'):
                verify(folder)

    def test_pgjt_78_signal_after_durable_completion_preserves_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            journal.append('environment', environment())
            journal.append('session-start', {'session': 0, 'pid': 101,
                           'backend_start': '2026-11-07T00:00:01+00:00'})
            append_constituent(journal)
            journal.fail('restoration', KeyboardInterrupt(), 'verified', None)
            result = verify(folder)
            self.assertEqual(result['outcome'], 'TERMINAL_FAIL')
            self.assertTrue(result['restoration_verified'])

    def test_protocol_binding_reads_declared_candidate_not_working_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(['git', 'init', '-q', repo], check=True)
            subprocess.run(['git', '-C', repo, 'config', 'user.email', 'test@example.invalid'], check=True)
            subprocess.run(['git', '-C', repo, 'config', 'user.name', 'Test'], check=True)
            (repo / 'protocol.py').write_text('version = 1\n')
            subprocess.run(['git', '-C', repo, 'add', 'protocol.py'], check=True)
            subprocess.run(['git', '-C', repo, 'commit', '-qm', 'v1'], check=True)
            candidate = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], check=True,
                                       text=True, capture_output=True).stdout.strip()
            (repo / 'protocol.py').write_text('version = 2\n')
            self.assertEqual(_candidate_blob(repo, candidate, 'protocol.py'), b'version = 1\n')

    def test_real_executor_stops_between_multi_statement_workload(self):
        plan = json.dumps([{'Plan': {'Node Type': 'Result', 'Actual Rows': 1, 'Actual Loops': 1},
                            'Planning Time': .1, 'Execution Time': .2}])
        class Session:
            def __init__(self): self.executed = []
            def execute(self, sql): self.executed.append(sql); return plan
        session = Session()
        def fail_after_first(index, statement, payload):
            if index == 0:
                raise RuntimeError('forced producer diagnostic fault: statement')
        with self.assertRaisesRegex(RuntimeError, 'forced producer'):
            explain(session, ['SELECT 1', 'SELECT 2'], fail_after_first)
        self.assertEqual(len(session.executed), 1)
        self.assertIn('SELECT 1', session.executed[0])

    def test_stability_trace_classes_remain_fail_closed(self):
        self.assertTrue(stable([1.0] * 10))
        self.assertFalse(stable([1.0] * 5 + [1.2] * 5))       # drift
        self.assertFalse(stable([1.0] * 5 + [1.3, .8, 1.3, .8, 1.3]))  # oscillation
        self.assertFalse(stable([.001] * 5 + [0.0] * 5))      # zero/quantised
        self.assertFalse(stable([1.0] * 5 + [1.0, 1.0, 9.0, 9.0, 9.0]))  # tail

    def test_invalid_provenance_unknown_events_and_post_terminal_append_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            invalid = provenance(folder)
            invalid['source_commit'] = 'not-a-commit'
            journal = DiagnosticJournal(folder, invalid)
            with self.assertRaises(RuntimeError):
                verify(folder)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            invalid = provenance(folder)
            invalid['executor_sha256'] = '0' * 64
            DiagnosticJournal(folder, invalid)
            with self.assertRaisesRegex(RuntimeError, 'protocol identity differs'):
                verify(folder)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            journal.append('arbitrary-event', {'execution_ms': -1})
            with self.assertRaisesRegex(RuntimeError, 'unknown diagnostic'):
                verify(folder)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            journal.fail('database-setup', RuntimeError('forced'), 'not-reached', None)
            with self.assertRaisesRegex(RuntimeError, 'already terminal'):
                journal.append('environment', environment())

    def test_terminal_failure_and_incomplete_prefix_are_not_publishable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            incomplete = verify(folder)
            self.assertEqual(incomplete["outcome"], "INCOMPLETE_PREFIX")
            self.assertFalse(incomplete["publishable"])
            self.assertFalse(incomplete["restoration_verified"])
            journal.fail("database-setup", RuntimeError("forced"), "not-reached", None)
            failed = verify(folder)
            self.assertEqual(failed["outcome"], "TERMINAL_FAIL")
            self.assertFalse(failed["publishable"])
            self.assertFalse(failed["restoration_verified"])

    def test_failure_phase_without_active_constituent_is_rejected(self):
        for phase in ["statement", "semantic-read-back", "rollback", "restoration"]:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                journal = DiagnosticJournal(folder, provenance(folder))
                journal.fail(phase, RuntimeError("forced " + phase), "not-reached", {"statement": 1})
                with self.assertRaisesRegex(RuntimeError, "no active constituent"):
                    verify(folder)

    def test_release_benchmark_verifier_rejects_diagnostic_marker_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            DiagnosticJournal(folder, provenance(folder))
            with self.assertRaisesRegex(RuntimeError, "never publishable"):
                verify_results(folder, candidate="1" * 40, trusted_key=folder / "absent.pem")

    def test_missing_manifest_is_recognised_incomplete_without_restoration_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            DiagnosticJournal(folder, provenance(folder))
            (folder / MANIFEST).unlink()
            result = verify(folder)
            self.assertEqual(result["outcome"], "INCOMPLETE_PREFIX")
            self.assertFalse(result["restoration_verified"])

    def test_tampering_truncation_reordering_and_duplicates_are_corrupt(self):
        mutations = []
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            journal = DiagnosticJournal(source, provenance(source))
            journal.fail("database-setup", RuntimeError("forced"), "not-reached", None)
            original = (source / JOURNAL).read_bytes().splitlines()
            incomplete = source / "incomplete"
            incomplete.mkdir()
            (incomplete / "installation.json").write_bytes((source / "installation.json").read_bytes())
            (incomplete / "installation.log").write_bytes((source / "installation.log").read_bytes())
            (incomplete / JOURNAL).write_bytes(original[0] + b"\n")
            self.assertEqual(verify(incomplete)["outcome"], "INCOMPLETE_PREFIX")
            mutations.extend([original + [original[-1]], list(reversed(original)),
                              [original[0], original[1][:-4]]])
            for lines in mutations:
                case = source / ("case-" + str(len(list(source.glob('case-*')))))
                case.mkdir()
                (case / "installation.json").write_bytes((source / "installation.json").read_bytes())
                (case / "installation.log").write_bytes((source / "installation.log").read_bytes())
                (case / JOURNAL).write_bytes(b"\n".join(lines) + b"\n")
                with self.assertRaises(RuntimeError):
                    verify(case)

    def test_atomic_manifest_failure_leaves_recognisable_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance(folder))
            real_fsync = os.fsync
            calls = 0
            def disk_full(descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated disk full")
                return real_fsync(descriptor)
            with mock.patch("bench.diagnostic_evidence.os.fsync", side_effect=disk_full):
                with self.assertRaises(OSError):
                    journal.append("environment", environment())
            self.assertEqual(verify(folder)["outcome"], "INCOMPLETE_PREFIX")

    def test_killed_writer_never_invents_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            script = """
import json,sys,time
from pathlib import Path
from bench.diagnostic_evidence import DiagnosticJournal
p=json.loads(sys.argv[2]); j=DiagnosticJournal(Path(sys.argv[1]),p)
print('ready',flush=True); time.sleep(60)
"""
            process = subprocess.Popen([sys.executable, "-c", script, str(folder),
                                        json.dumps(provenance(folder))], cwd=ROOT,
                                       stdout=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)
            process.stdout.close()
            result = verify(folder)
            self.assertEqual(result["outcome"], "INCOMPLETE_PREFIX")
            self.assertFalse(result["restoration_verified"])

    def test_sigterm_and_corrupt_installation_remain_untrusted(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            script = """
import json,sys,time
from pathlib import Path
from bench.diagnostic_evidence import DiagnosticJournal
DiagnosticJournal(Path(sys.argv[1]),json.loads(sys.argv[2])); print('ready',flush=True); time.sleep(60)
"""
            process = subprocess.Popen([sys.executable, "-c", script, str(folder),
                                        json.dumps(provenance(folder))], cwd=ROOT,
                                       stdout=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=10); process.stdout.close()
            self.assertEqual(verify(folder)["outcome"], "INCOMPLETE_PREFIX")
            (folder / "installation.log").write_text("corrupt\n")
            with self.assertRaisesRegex(RuntimeError, "installation log differs"):
                verify(folder)


if __name__ == "__main__":
    unittest.main()
