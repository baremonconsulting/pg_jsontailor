#!/usr/bin/env python3
"""Fail when the repository is not a self-consistent release candidate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.product_fingerprint import PRODUCT_INPUTS, SCHEMA_VERSION, digest
from bench.installation import verify_attestation


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_EQUIVALENCE_PATHS = PRODUCT_INPUTS
EXACT_SHA_CHECKS = (
    "Repository checks", "PostgreSQL 17", "PostgreSQL 18", "PostgreSQL 19",
    "CodeQL (Python)", "Dependency review")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True,
                          capture_output=True, check=check)


def product_hashes(repo: Path, commit: str,
                   product_paths: Sequence[str] = PRODUCT_EQUIVALENCE_PATHS,
                   fingerprint_schema: int = SCHEMA_VERSION,
                   ) -> dict[str, str] | None:
    listing = subprocess.run(
        ["git", "ls-tree", "-rz", "--full-tree", commit, "--", *product_paths],
        cwd=repo, capture_output=True)
    if listing.returncode:
        return None
    hashes: dict[str, str] = {}
    for entry in (value for value in listing.stdout.split(b"\0") if value):
        try:
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, _object = metadata.split(b" ", 2)
        except ValueError:
            return None
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            return None
        name = os.fsdecode(raw_name)
        content = subprocess.run(["git", "show", f"{commit}:{name}"], cwd=repo,
                                 capture_output=True)
        if content.returncode:
            return None
        hashes[name] = digest(name, content.stdout, mode.decode(), fingerprint_schema)
    return hashes


def benchmark_source_status(repo: Path, source_commit: str,
                            candidate: str = "HEAD",
                            product_paths: Sequence[str] = PRODUCT_EQUIVALENCE_PATHS,
                            recorded_hashes: dict[str, str] | None = None,
                            fingerprint_schema: int = SCHEMA_VERSION,
                            ) -> tuple[bool, str]:
    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=repo, text=True,
                              capture_output=True)

    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        return False, "reference benchmark source commit is invalid"
    if source_commit == "0" * 40:
        return False, "reference benchmark source commit is invalid"
    if recorded_hashes is not None:
        if not recorded_hashes:
            return False, "reference benchmark product fingerprints are invalid"
        candidate_hashes = product_hashes(repo, candidate, product_paths,
                                          fingerprint_schema)
        if candidate_hashes is None:
            return False, "candidate product inputs cannot be read"
        if candidate_hashes != recorded_hashes:
            return False, "product SQL changed after the current reference benchmark"
        if run("cat-file", "-e", source_commit + "^{commit}").returncode == 0:
            source_hashes = product_hashes(repo, source_commit, product_paths,
                                           fingerprint_schema)
            if source_hashes != recorded_hashes:
                return False, "reference benchmark fingerprints differ from its source commit"
        return True, "product-equivalent benchmark fingerprint"
    if run("cat-file", "-e", source_commit + "^{commit}").returncode:
        return False, "reference benchmark commit is absent"
    if run("merge-base", "--is-ancestor", source_commit, candidate).returncode:
        return False, "reference benchmark commit is not an ancestor"
    if fingerprint_schema == 1:
        if run("diff", "--quiet", source_commit, candidate, "--", *product_paths).returncode:
            return False, "product SQL changed after the current reference benchmark"
    else:
        source_hashes = product_hashes(repo, source_commit, product_paths,
                                       fingerprint_schema)
        candidate_hashes = product_hashes(repo, candidate, product_paths,
                                          fingerprint_schema)
        if source_hashes is None or candidate_hashes is None:
            return False, "benchmark product inputs cannot be read"
        if source_hashes != candidate_hashes:
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


def verify_benchmark_candidate(folder: Path, candidate: str, trusted_key: Path,
                               repo: Path = ROOT) -> dict:
    """Release consumer: independently authenticate installation before timings."""
    installation = verify_attestation(folder / 'installation.json', repo=repo,
                                      candidate=candidate, trusted_key=trusted_key)
    from bench.verify_results import verify_results
    result = verify_results(folder, candidate=candidate, trusted_key=trusted_key, repo=repo)
    require(installation['candidate'] == candidate, 'release benchmark candidate differs')
    return result


def verify_benchmark_matrix(folders: Sequence[Path], candidate: str,
                            trusted_key: Path, repo: Path = ROOT) -> None:
    from tools.strict_results import loads
    require(len(folders) == 3, 'release requires exact-candidate benchmarks on PG17/18/19')
    majors = []
    for folder in folders:
        verify_benchmark_candidate(folder, candidate, trusted_key, repo)
        majors.append(loads((folder / 'environment.json').read_text())['postgresql_major'])
    require(set(majors) == {'17', '18', '19'}, 'release benchmark majors differ/duplicate')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument('--benchmark-evidence', action='append', type=Path, default=[])
    parser.add_argument('--benchmark-key', type=Path)
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

    if args.benchmark_evidence:
        require(args.benchmark_key is not None, 'independently trusted benchmark key required')
        verify_benchmark_matrix(args.benchmark_evidence, git('rev-parse', 'HEAD').stdout.strip(),
                                args.benchmark_key)

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

    benchmark = 'exact-candidate matrix PASS' if args.benchmark_evidence else 'final benchmark PENDING'
    print(f"release candidate {version}: PASS ({benchmark}; not release approval)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
