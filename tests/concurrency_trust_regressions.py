"""A failing, early-exit or uncommitted background writer cannot pass."""
from pathlib import Path
import sys
from unittest import mock
import unittest
from concurrent_writer import held_writer, require_commit, wait_for


class WriterEvidence(unittest.TestCase):
    def test_failing_writer_never_reaches_barrier(self):
        process = mock.Mock()
        process.poll.return_value = 3
        process.returncode = 3
        process.communicate.return_value = ("BEGIN\n", "ERROR: division by zero")
        with self.assertRaisesRegex(RuntimeError, "exited before lock barrier"):
            wait_for(lambda sql: "t", process, "SELECT true", "writer")

    def test_missing_lock_fails_boundedly(self):
        process = mock.Mock(); process.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, "did not acquire expected lock"):
            wait_for(lambda sql: "f", process, "SELECT false", "writer", timeout=0)

    def test_return_code_and_commit_are_both_required(self):
        for rc, output in [(1, "COMMIT\n"), (0, "ROLLBACK\n"), (0, "UPDATE 1\n")]:
            process = mock.Mock(); process.returncode = rc
            process.communicate.return_value = (output, "")
            with self.subTest(rc=rc, output=output), self.assertRaisesRegex(RuntimeError, "did not commit"):
                require_commit(process)

    def test_real_sql_failure_is_rejected_when_database_is_supplied(self):
        import os
        import subprocess
        psql = os.environ.get("PGJT_TRUST_PSQL")
        database = os.environ.get("PGJT_TRUST_DATABASE")
        if not psql or not database:
            self.skipTest("set PGJT_TRUST_PSQL and PGJT_TRUST_DATABASE for live failing-writer regression")
        command = [psql, "-XAt", "-v", "ON_ERROR_STOP=1", "-d", database]
        def query(statement):
            return subprocess.run(command, input=statement, text=True, capture_output=True,
                                  check=True, timeout=5).stdout.strip()
        with self.assertRaisesRegex(RuntimeError, "exited before lock barrier"):
            with held_writer(command, query, "SELECT 1/0;", "pg_class"):
                self.fail("failing writer crossed the lock barrier")


if __name__ == "__main__":
    unittest.main()
