#!/usr/bin/env python3
"""Bind standalone runs to a checkout or a checksum-verified extracted archive."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


def archive_provenance(root: Path, archive_path: Path, source_commit: str,
                       expected_checksum: str) -> dict:
    if re.fullmatch(r"[0-9a-f]{40}", source_commit or "") is None:
        raise RuntimeError("archive run requires an explicit full source commit")
    if re.fullmatch(r"[0-9a-f]{64}", expected_checksum or "") is None:
        raise RuntimeError("archive run requires an independently supplied SHA-256 checksum")
    checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if checksum != expected_checksum:
        raise RuntimeError("source archive checksum differs from the supplied manifest")
    expected = {}
    archive_root = None
    with tarfile.open(archive_path, "r:*") as archive:
        seen = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise RuntimeError("unsafe source archive path")
            if member.name in seen:
                raise RuntimeError("duplicate source archive path")
            seen.add(member.name)
            if archive_root is None:
                archive_root = path.parts[0]
            if path.parts[0] != archive_root:
                raise RuntimeError("source archive must have exactly one root")
            if member.isdir():
                continue
            if not member.isfile() or len(path.parts) < 2:
                raise RuntimeError("source archive contains a non-regular file")
            relative = str(PurePosixPath(*path.parts[1:]))
            if relative in expected or ".git" in path.parts:
                raise RuntimeError("duplicate or Git-internal source archive member")
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("source archive member cannot be read")
            expected[relative] = {"sha256": hashlib.sha256(stream.read()).hexdigest(),
                                  "executable": bool(member.mode & 0o111)}
    if not expected:
        raise RuntimeError("source archive contains no source files")
    observed = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("extracted source tree contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        # Bytecode produced by another smoke suite is not an additional source input.
        if "__pycache__" in relative.parts and path.suffix == ".pyc":
            continue
        observed[relative.as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "executable": bool(path.stat().st_mode & 0o111)}
    if observed != expected:
        raise RuntimeError("extracted source inventory/content differs from the verified archive")
    manifest = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    return {"source_commit": source_commit, "dirty_tree": False,
            "source_provenance": "archive-declared", "source_archive_sha256": checksum,
            "source_manifest_sha256": hashlib.sha256(manifest).hexdigest()}


def source_identity(root: Path, source_commit: str | None = None,
                    archive: Path | None = None, checksum: str | None = None) -> dict:
    checkout = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                              text=True, capture_output=True)
    if checkout.returncode == 0 and Path(checkout.stdout.strip()).resolve() == root.resolve():
        detected = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        if source_commit is not None and source_commit != detected:
            raise RuntimeError("explicit source commit differs from Git HEAD")
        if archive is not None or checksum is not None:
            raise RuntimeError("archive provenance requires an actual extracted non-Git tree")
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
        return {"source_commit": detected, "dirty_tree": dirty,
                "source_provenance": "git-checkout"}
    if source_commit is None or archive is None or checksum is None:
        raise RuntimeError("outside Git, supply --source-commit, --source-archive and --archive-sha256")
    return archive_provenance(root, archive, source_commit, checksum)
