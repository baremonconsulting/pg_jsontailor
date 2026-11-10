"""Bounded two-session lock barrier that proves writer startup and commit."""
from __future__ import annotations

from contextlib import contextmanager
import secrets
import subprocess
import time


def wait_for(query, process, predicate, label, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"{label} exited before lock barrier: rc={process.returncode}; {stdout}; {stderr}")
        if query(predicate) == "t":
            return
        time.sleep(.02)
    raise RuntimeError(f"{label} did not acquire expected lock within {timeout}s")


def require_commit(process, timeout=5):
    stdout, stderr = process.communicate(timeout=timeout)
    if process.returncode != 0 or stdout.splitlines()[-1:] != ["COMMIT"]:
        raise RuntimeError(f"writer did not commit: rc={process.returncode}; {stdout}; {stderr}")


@contextmanager
def held_writer(command, query, statement, relation):
    token = secrets.randbelow(1_000_000_000) + 1
    controller_name, writer_name = f"jt_controller_{token}", f"jt_writer_{token}"
    controller = writer = None
    try:
        controller = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
        controller.stdin.write(f"SET application_name='{controller_name}';BEGIN;SELECT pg_advisory_xact_lock({token});\n")
        controller.stdin.flush()
        wait_for(query, controller,
                 f"SELECT EXISTS(SELECT 1 FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid "
                 f"WHERE a.application_name='{controller_name}' AND l.locktype='advisory' AND l.granted);",
                 "controller")
        writer = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        writer.stdin.write(f"\\set VERBOSITY verbose\nSET application_name='{writer_name}';"
                           f"SET statement_timeout='10s';BEGIN;{statement}"
                           f"SELECT pg_advisory_xact_lock({token});COMMIT;\n")
        writer.stdin.flush()
        wait_for(query, writer,
                 f"SELECT EXISTS(SELECT 1 FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid "
                 f"WHERE a.application_name='{writer_name}' AND a.wait_event='advisory' "
                 f"AND l.relation='{relation}'::regclass AND l.mode IN ('RowExclusiveLock','RowShareLock') AND l.granted);",
                 "writer")
        yield writer
        controller.stdin.write("COMMIT;\n")
        controller.stdin.flush()
        require_commit(controller)
        require_commit(writer)
    finally:
        for process in (controller, writer):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
