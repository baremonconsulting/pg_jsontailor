#!/usr/bin/env python3
"""Fail closed on missing/incomplete standalone and source-archive notices."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
# Independently pinned legal text: prose normalisation must never rewrite it.
LICENCE_SHA256 = "c1406d6bfc8c4c415ebd744ac412dcf890c8c40a59eba13aeaf8d2c8aafeb791"
RUNTIME_REFERENCE = re.compile(r'(?i)(?<![a-z0-9_])"?jsontailor"?\s*\.')


def verify_notice(notice: bytes, licence: bytes) -> None:
    if hashlib.sha256(licence).hexdigest() != LICENCE_SHA256:
        raise RuntimeError("canonical LICENSE differs from the reviewed legal text")
    if licence not in notice:
        raise RuntimeError("generated SQL notice lacks the complete verbatim LICENSE")


def verify_bundle(bundle: Path) -> None:
    verify_notice((bundle / "GENERATED_SQL_NOTICE").read_bytes(), (ROOT / "LICENSE").read_bytes())
    generated = bundle / "generated.sql"
    if not generated.is_file() or not generated.read_text().strip():
        raise RuntimeError("standalone bundle has no generated.sql")
    if RUNTIME_REFERENCE.search(generated.read_text()):
        raise RuntimeError("standalone SQL contains a runtime jsontailor reference")


def verify_source(source: Path) -> None:
    verify_notice((source / "GENERATED_SQL_NOTICE").read_bytes(), (source / "LICENSE").read_bytes())
    if not (source / "examples/standalone/README.md").is_file():
        raise RuntimeError("source package lacks the standalone deployment example")


def verify_archive(path: Path) -> None:
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise RuntimeError("source archive contains duplicate paths")
        roots = {PurePosixPath(name).parts[0] for name in names
                 if PurePosixPath(name).parts}
        if len(roots) != 1:
            raise RuntimeError("source archive must have one root")
        prefix = next(iter(roots)) + "/"

        def read(name: str) -> bytes:
            member = archive.getmember(prefix + name)
            if not member.isfile():
                raise RuntimeError("required package notice/example is not a regular file")
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError("required package file cannot be read")
            return stream.read()

        verify_notice(read("GENERATED_SQL_NOTICE"), read("LICENSE"))
        read("examples/standalone/README.md")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--bundle", type=Path)
    group.add_argument("--archive", type=Path)
    group.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.bundle:
        verify_bundle(args.bundle)
    elif args.archive:
        verify_archive(args.archive)
    else:
        verify_source(args.source or ROOT)
    print("generated SQL notices: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
