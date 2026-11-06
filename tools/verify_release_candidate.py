#!/usr/bin/env python3
"""Fail when the repository is not a self-consistent release candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "bench/reference/azure-11e6d74"
PRODUCT_EQUIVALENCE_PATHS = ("sql", "pg_jsontailor.control", "Makefile")
EXACT_SHA_CHECKS = (
    "Repository checks", "PostgreSQL 17", "PostgreSQL 18", "PostgreSQL 19",
    "CodeQL (Python)", "Dependency review")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True,
                          capture_output=True, check=check)


def benchmark_source_status(repo: Path, source_commit: str,
                            candidate: str = "HEAD",
                            product_paths: Sequence[str] = PRODUCT_EQUIVALENCE_PATHS
                            ) -> tuple[bool, str]:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=repo, text=True,
                              capture_output=True)

    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        return False, "reference benchmark source commit is invalid"
    if run("cat-file", "-e", source_commit + "^{commit}").returncode:
        return False, "reference benchmark commit is absent"
    if run("merge-base", "--is-ancestor", source_commit, candidate).returncode:
        return False, "reference benchmark commit is not an ancestor"
    if run("diff", "--quiet", source_commit, candidate, "--", *product_paths).returncode:
        return False, "product SQL changed after the current reference benchmark"
    return True, "product-equivalent benchmark source"


def verify_release_policy(release: str, dependency: str) -> None:
    block = re.search(r"required_checks=\(\n(.*?)\n\s+\)\n", release, re.DOTALL)
    require(block is not None, "release gate required-check list is missing")
    names = tuple(re.findall(r"'([^']+)'", block.group(1)))
    require(names == EXACT_SHA_CHECKS,
            "release gate exact-SHA check list differs")
    require("push:\n    branches: [main]" in dependency,
            "dependency review does not run on main pushes")
    require("pull_request:\n    branches: [main]" in dependency,
            "dependency review does not run on main pull requests")
    require("BASE_REF: ${{ github.event.before }}" in dependency
            and "test \"$BASE_REF\" != '0000000000000000000000000000000000000000'" in dependency
            and "git cat-file -e \"$BASE_REF^{commit}\"" in dependency,
            "dependency review main comparison does not fail closed")
    require("base-ref: ${{ github.event.before }}" in dependency
            and "head-ref: ${{ github.sha }}" in dependency,
            "dependency review main comparison range is not exact")
    action = "actions/dependency-review-action@2031cfc080254a8a887f58cffee85186f0e49e48"
    require(dependency.count(action) == 2,
            "dependency review PR and main runs are not equally pinned")


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
    benchmark_ok, benchmark_detail = benchmark_source_status(ROOT, source_commit)
    require(benchmark_ok, benchmark_detail)

    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    require("tests/dump_restore.py" in workflow,
            "CI does not run dump/restore verification")
    release = (ROOT / ".github/workflows/release.yml").read_text()
    dependency = (ROOT / ".github/workflows/dependency-review.yml").read_text()
    verify_release_policy(release, dependency)

    forbidden = git("grep", "-inE",
                    r"ChatGPT|Codex|Claude|artificial intelligence",
                    "--", "*.md", "*.py", "*.sql", "*.yml", "*.yaml",
                    ":(exclude)tools/verify_release_candidate.py",
                    check=False)
    require(forbidden.returncode == 1,
            "public source contains disallowed authorship/tooling references:\n" + forbidden.stdout)

    print(f"release candidate {version}: PASS ({source_commit[:8]} product-equivalent benchmark)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
