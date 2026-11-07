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


def provenance() -> dict:
    return {"source_commit": "1" * 40, "source_dirty": False,
            "installation_sha256": "2" * 64, "installation_log_sha256": "3" * 64,
            "workload_sha256": "4" * 64, "producer_sha256": "5" * 64,
            "command": ["python3", "bench/duality_overhead.py"],
            "started_at": "2026-11-07T00:00:00+00:00"}


class DiagnosticTrust(unittest.TestCase):
    def test_terminal_failure_and_incomplete_prefix_are_not_publishable(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance())
            journal.append("constituent-start", {"session": 0})
            incomplete = verify_diagnostic(folder)
            self.assertEqual(incomplete["outcome"], "INCOMPLETE_PREFIX")
            self.assertFalse(incomplete["publishable"])
            self.assertFalse(incomplete["restoration_verified"])
            journal.fail("statement", RuntimeError("forced"), "verified", {"statement": 0})
            failed = verify_diagnostic(folder)
            self.assertEqual(failed["outcome"], "TERMINAL_FAIL")
            self.assertFalse(failed["publishable"])
            self.assertTrue(failed["restoration_verified"])

    def test_every_required_interruption_phase_is_retained(self):
        phases = ["between-arms", "statement", "semantic-read-back", "rollback", "restoration"]
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                journal = DiagnosticJournal(folder, provenance())
                journal.append("constituent-start", {"phase": phase})
                journal.fail(phase, RuntimeError("forced " + phase),
                             "failed" if phase in {"rollback", "restoration"} else "not-reached",
                             {"statement": 1})
                result = verify_diagnostic(folder)
                self.assertEqual(result["outcome"], "TERMINAL_FAIL")
                self.assertFalse(result["publishable"])

    def test_release_benchmark_verifier_rejects_diagnostic_marker_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            DiagnosticJournal(folder, provenance())
            with self.assertRaisesRegex(RuntimeError, "never publishable"):
                verify_results(folder, candidate="1" * 40, trusted_key=folder / "absent.pem")

    def test_missing_manifest_is_recognised_incomplete_without_restoration_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            DiagnosticJournal(folder, provenance())
            (folder / MANIFEST).unlink()
            result = verify_diagnostic(folder)
            self.assertEqual(result["outcome"], "INCOMPLETE_PREFIX")
            self.assertFalse(result["restoration_verified"])

    def test_tampering_truncation_reordering_and_duplicates_are_corrupt(self):
        mutations = []
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            journal = DiagnosticJournal(source, provenance())
            journal.append("statement-complete", {"statement": 0})
            original = (source / JOURNAL).read_bytes().splitlines()
            incomplete = source / "incomplete"
            incomplete.mkdir()
            (incomplete / JOURNAL).write_bytes(original[0] + b"\n")
            self.assertEqual(verify_diagnostic(incomplete)["outcome"], "INCOMPLETE_PREFIX")
            mutations.extend([original + [original[-1]], list(reversed(original)),
                              [original[0], original[1][:-4]]])
            for lines in mutations:
                case = source / ("case-" + str(len(list(source.glob('case-*')))))
                case.mkdir()
                (case / JOURNAL).write_bytes(b"\n".join(lines) + b"\n")
                with self.assertRaises(RuntimeError):
                    verify_diagnostic(case)

    def test_atomic_manifest_failure_leaves_recognisable_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            journal = DiagnosticJournal(folder, provenance())
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
                    journal.append("statement-complete", {"statement": 0})
            self.assertEqual(verify_diagnostic(folder)["outcome"], "INCOMPLETE_PREFIX")

    def test_killed_writer_never_invents_terminal_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            script = """
import json,sys,time
from pathlib import Path
from bench.diagnostic_evidence import DiagnosticJournal
p=json.loads(sys.argv[2]); j=DiagnosticJournal(Path(sys.argv[1]),p)
j.append('constituent-start', {'session':0}); print('ready',flush=True); time.sleep(60)
"""
            process = subprocess.Popen([sys.executable, "-c", script, str(folder),
                                        json.dumps(provenance())], cwd=Path(__file__).resolve().parents[1],
                                       stdout=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)
            process.stdout.close()
            result = verify_diagnostic(folder)
            self.assertEqual(result["outcome"], "INCOMPLETE_PREFIX")
            self.assertFalse(result["restoration_verified"])


if __name__ == "__main__":
    unittest.main()
