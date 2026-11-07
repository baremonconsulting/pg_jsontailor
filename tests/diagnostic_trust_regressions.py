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
from bench.diagnostic_evidence import DiagnosticJournal, MANIFEST, JOURNAL, verify_diagnostic
from bench.verify_results import verify_results
from bench.verify_results import stable
from bench.installation import sha256
from bench.benchmark_execution import explain

ROOT = Path(__file__).resolve().parents[1]

def provenance(folder: Path) -> dict:
    (folder / "installation.json").write_text("{}\n")
    (folder / "installation.log").write_text("test installation log\n")
    return {"source_commit": "1" * 40, "source_dirty": False,
            "installation_sha256": sha256((folder / "installation.json").read_bytes()),
            "installation_log_sha256": sha256((folder / "installation.log").read_bytes()),
            "workload_sha256": sha256((ROOT / "bench/workload.py").read_bytes()),
            "producer_sha256": sha256((ROOT / "bench/duality_overhead.py").read_bytes()),
            "command": ["python3", "bench/duality_overhead.py"],
            "started_at": "2026-11-07T00:00:00+00:00"}

def verify(folder: Path):
    with mock.patch("bench.diagnostic_evidence.verify_attestation", return_value={'postgresql': '18.6'}):
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
            'stability_threshold': .10, 'repetitions': 50, 'sessions': 3}


class DiagnosticTrust(unittest.TestCase):
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
