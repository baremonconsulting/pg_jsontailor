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


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "bench/reference/azure-039a555-pg18"
REFERENCE_FINGERPRINT = REFERENCE / "fingerprint-v2.json"
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
    migration = json.loads(REFERENCE_FINGERPRINT.read_text())
    require(migration.get("product_fingerprint_schema") == SCHEMA_VERSION,
            "reference benchmark fingerprint schema differs")
    require(migration.get("source_commit") == environment.get("source_commit"),
            "reference fingerprint source differs from benchmark environment")
    require(migration.get("original_product_hashes") == environment.get("product_hashes"),
            "reference fingerprint migration does not preserve original hashes")
    source_commit = environment.get("source_commit", "")
    benchmark_ok, benchmark_detail = benchmark_source_status(
        ROOT, source_commit, recorded_hashes=migration.get("product_hashes"),
        fingerprint_schema=migration.get("product_fingerprint_schema"))
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
