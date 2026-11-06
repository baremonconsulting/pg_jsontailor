#!/usr/bin/env python3
"""Sample backend RSS and CPU ticks during repeated stress-profile evaluation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def backend_pid(psql: str, database: str, application_name: str) -> int | None:
    query = ("SELECT pid FROM pg_catalog.pg_stat_activity "
             f"WHERE application_name = '{application_name}' "
             "AND state = 'active' ORDER BY backend_start DESC LIMIT 1")
    found = subprocess.run([psql, "-XqAt", "-d", database, "-c", query],
                           text=True, capture_output=True, check=True).stdout.strip()
    return int(found) if found else None


def sample(pid: int) -> tuple[int, int]:
    status = Path(f"/proc/{pid}/status").read_text()
    rss_line = next(line for line in status.splitlines() if line.startswith("VmRSS:"))
    rss_kib = int(rss_line.split()[1])
    fields = Path(f"/proc/{pid}/stat").read_text().split()
    cpu_ticks = int(fields[13]) + int(fields[14])
    return rss_kib, cpu_ticks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--database", required=True)
    parser.add_argument("--function", required=True)
    parser.add_argument("--implementation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=200)
    args = parser.parse_args()
    app = f"pgjt_resource_{args.implementation}"
    query = f"""
SET jit = off;
SET max_parallel_workers_per_gather = 0;
SELECT sum(pg_catalog.pg_column_size(
           {args.function}(b.target, b.patch)))
FROM bench_inputs AS b
CROSS JOIN pg_catalog.generate_series(1, {args.repeat}) AS repetitions(n)
WHERE b.profile = 'stress_256k';
"""
    environment = os.environ.copy()
    environment["PGAPPNAME"] = app
    started = time.monotonic()
    process = subprocess.Popen([args.psql, "-XqAt", "-v", "ON_ERROR_STOP=1",
                                "-d", args.database, "-c", query], text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=environment)
    pid = None
    for _ in range(100):
        pid = backend_pid(args.psql, args.database, app)
        if pid is not None:
            break
        if process.poll() is not None:
            break
        time.sleep(0.01)
    samples = []
    while pid is not None and process.poll() is None:
        try:
            rss_kib, cpu_ticks = sample(pid)
            samples.append({"elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                            "rss_kib": rss_kib, "cpu_ticks": cpu_ticks})
        except FileNotFoundError:
            break
        time.sleep(0.01)
    stdout, stderr = process.communicate()
    result = {
        "implementation": args.implementation,
        "function": args.function,
        "repeat": args.repeat,
        "backend_pid": pid,
        "sampling_interval_ms": 10,
        "sample_count": len(samples),
        "peak_rss_kib": max((row["rss_kib"] for row in samples), default=None),
        "cpu_ticks_delta": ((samples[-1]["cpu_ticks"] - samples[0]["cpu_ticks"])
                            if len(samples) > 1 else None),
        "wall_ms": round((time.monotonic() - started) * 1000, 3),
        "returncode": process.returncode,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
