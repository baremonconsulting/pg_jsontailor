#!/usr/bin/env python3
"""Fail when the repository is not a self-consistent release candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "bench/reference/azure-11e6d74"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True,
                          capture_output=True, check=check)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    if not args.allow_dirty:
        require(not git("status", "--porcelain").stdout,
                "release candidate worktree is dirty")

    control = (ROOT / "pg_jsontailor.control").read_text()
    match = re.search(r"^default_version = '([^']+)'$", control, re.MULTILINE)
    require(match is not None, "extension version is missing")
    version = match.group(1)
    require(f"## {version} — unreleased" in (ROOT / "CHANGELOG.md").read_text(),
            "changelog is not in release-candidate state")
    require(f"version: {version}" in (ROOT / "CITATION.cff").read_text(),
            "citation version differs")
    require("Baremon s.r.o." in (ROOT / "LICENSE").read_text(),
            "legal identity differs")

    environment = json.loads((REFERENCE / "environment.json").read_text())
    source_commit = environment.get("source_commit", "")
    require(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None,
            "reference benchmark source commit is invalid")
    require(git("cat-file", "-e", source_commit + "^{commit}", check=False).returncode == 0,
            "reference benchmark commit is absent")
    require(git("merge-base", "--is-ancestor", source_commit, "HEAD", check=False).returncode == 0,
            "reference benchmark commit is not an ancestor")
    require(git("diff", "--quiet", source_commit, "HEAD", "--",
                "sql", "pg_jsontailor.control", "Makefile", check=False).returncode == 0,
            "product SQL changed after the current reference benchmark")

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    require("tests/dump_restore.py" in workflow,
            "CI does not run dump/restore verification")
    release = (ROOT / ".github/workflows/release.yml").read_text()
    for name in ("Repository checks", "PostgreSQL 17", "PostgreSQL 18",
                 "PostgreSQL 19", "CodeQL (Python)", "Dependency review"):
        require(name in release, f"release gate is missing required check {name}")

    forbidden = git("grep", "-inE",
                    r"ChatGPT|Codex|Claude|artificial intelligence",
                    "--", "*.md", "*.py", "*.sql", "*.yml", "*.yaml",
                    ":(exclude)tools/verify_release_candidate.py",
                    check=False)
    require(forbidden.returncode == 1,
            "public source contains disallowed authorship/tooling references:\n" + forbidden.stdout)

    print(f"release candidate {version}: PASS ({source_commit[:8]} benchmark)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
