#!/usr/bin/env python3
"""Install PGDG packages within a hard budget, retaining every diagnostic."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import time


def validate_budget(budget, job_minutes, reserve_minutes):
    if not (0 < budget <= 480 and reserve_minutes >= 25
            and budget + reserve_minutes * 60 + 120 <= job_minutes * 60):
        raise RuntimeError("installation budget must reserve 25 minutes for tests plus overhead")


def install(major, log, budget=480, job_minutes=45, reserve_minutes=25, *, run=subprocess.run):
    validate_budget(budget, job_minutes, reserve_minutes)
    if major not in {"17", "18"}:
        raise RuntimeError("package installer supports PostgreSQL 17 and 18 only")
    log.parent.mkdir(parents=True, exist_ok=True)
    steps = [
        ["sudo", "install", "-d", "-m", "0755", "/usr/share/postgresql-common/pgdg"],
        ["bash", "-euo", "pipefail", "-c",
         "curl --proto '=https' --tlsv1.2 --retry 2 --retry-all-errors --connect-timeout 10 "
         "--max-time 30 -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | "
         "gpg --dearmor | sudo tee /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg >/dev/null"],
        ["bash", "-euo", "pipefail", "-c",
         '. /etc/os-release; echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] '
         'https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" '
         '| sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null'],
        ["sudo", "apt-get", "-o", "Acquire::Retries=2", "-o", "Acquire::http::Timeout=20",
         "-o", "Acquire::https::Timeout=20", "-o", "DPkg::Lock::Timeout=30", "update"],
        ["sudo", "apt-get", "-y", "-o", "Acquire::Retries=2", "-o", "Acquire::http::Timeout=20",
         "-o", "Acquire::https::Timeout=20", "-o", "DPkg::Lock::Timeout=30", "install",
         "--no-install-recommends", f"postgresql-{major}", f"postgresql-server-dev-{major}"],
    ]
    deadline = time.monotonic() + budget
    with log.open("w") as diagnostics:
        for command in steps:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("installation exhausted its bounded budget")
            diagnostics.write(f"remaining_seconds={remaining:.2f}; command={command!r}\n")
            diagnostics.flush()
            try:
                run(command, check=True, stdout=diagnostics, stderr=subprocess.STDOUT,
                    timeout=min(180, remaining))
            except (subprocess.SubprocessError, OSError) as error:
                diagnostics.write(f"installation failed: {error}\n")
                raise RuntimeError(f"bounded PostgreSQL installation failed; inspect {log}") from error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("major")
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--budget-seconds", type=int, default=480)
    parser.add_argument("--job-minutes", type=int, default=45)
    parser.add_argument("--test-reserve-minutes", type=int, default=25)
    args = parser.parse_args()
    install(args.major, args.log, args.budget_seconds, args.job_minutes, args.test_reserve_minutes)


if __name__ == "__main__":
    main()
