#!/usr/bin/env python3
"""Fail closed on missing/incomplete standalone and source-archive notices."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
# Independently pinned legal text: prose normalisation must never rewrite it.
LICENCE_SHA256 = "c1406d6bfc8c4c415ebd744ac412dcf890c8c40a59eba13aeaf8d2c8aafeb791"
# Reviewed installed pg_get_functiondef hashes, not values learned from a bundle
# or its exporting database. A changed generator requires deliberate re-review.
EXPECTED_GENERATOR_SHA256 = {
    ("0.2.0", major): "1d2fd5ab071a8c9c76a1170a9440667dd6619ee266bbacc05e3ccf9311b38e53"
    for major in (17, 18, 19)
}
RUNTIME_REFERENCE = re.compile(r'(?i)(?<![a-z0-9_])"?jsontailor"?\s*\.')
BUNDLE_HEADER = re.compile(
    r'\A-- pg_jsontailor standalone bundle\n'
    r'-- Generator version: ([0-9]+\.[0-9]+\.[0-9]+)\n'
    r'-- Generator PostgreSQL major: ([0-9]+)\n'
    r'-- Target view: ("[^\n]*")\n'
    r'-- Generator SHA-256: ([0-9a-f]{64})\n')


def verify_generator(version: str, major: int, generator_hash: str) -> None:
    """Reject unknown versions/majors and stale or altered installed generators."""
    expected = EXPECTED_GENERATOR_SHA256.get((version, major))
    if expected is None or generator_hash != expected:
        raise RuntimeError("standalone generator hash differs from the reviewed candidate")


def bundle_preamble(version: str, major: int, view: str, notice: str,
                    generator_hash: str) -> str:
    """Canonical self-contained transaction envelope, shared with the exporter."""
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise RuntimeError("invalid standalone generator version")
    if type(major) is not int or major < 10 or not isinstance(view, str) or not view:
        raise RuntimeError("invalid standalone generator major or target view")
    if not isinstance(generator_hash, str) or re.fullmatch(r'[0-9a-f]{64}', generator_hash) is None:
        raise RuntimeError("invalid standalone generator hash")
    commented_notice = ''.join('-- ' + line + '\n' for line in notice.splitlines())
    return (
        '-- pg_jsontailor standalone bundle\n'
        f'-- Generator version: {version}\n'
        f'-- Generator PostgreSQL major: {major}\n'
        f'-- Target view: {json.dumps(view, ensure_ascii=True)}\n'
        f'-- Generator SHA-256: {generator_hash}\n'
        '-- BEGIN GENERATED_SQL_NOTICE\n' + commented_notice +
        '-- END GENERATED_SQL_NOTICE\n\n'
        'BEGIN;\n'
        'SET LOCAL search_path = pg_catalog, pg_temp;\n'
        'DO $pg_jsontailor_bundle$\n'
        'DECLARE\n'
        "    target_major integer := pg_catalog.current_setting('server_version_num')::integer / 10000;\n"
        'BEGIN\n'
        f'    IF target_major <> {major} THEN\n'
        "        RAISE EXCEPTION 'standalone bundle generated on PostgreSQL %, target PostgreSQL %; matching major versions required',\n"
        f"            {major}, target_major USING ERRCODE = '0A000';\n"
        '    END IF;\n'
        'END;\n'
        '$pg_jsontailor_bundle$;\n\n')


def verify_notice(notice: bytes, licence: bytes) -> None:
    if hashlib.sha256(licence).hexdigest() != LICENCE_SHA256:
        raise RuntimeError("canonical LICENSE differs from the reviewed legal text")
    if licence not in notice:
        raise RuntimeError("generated SQL notice lacks the complete verbatim LICENSE")


def verify_bundle(bundle: Path) -> None:
    notice = (bundle / "GENERATED_SQL_NOTICE").read_bytes()
    verify_notice(notice, (ROOT / "LICENSE").read_bytes())
    if (ROOT / "GENERATED_SQL_NOTICE").read_bytes() not in notice:
        raise RuntimeError("standalone bundle lacks the complete GENERATED_SQL_NOTICE")
    generated = bundle / "generated.sql"
    if not generated.is_file() or not generated.read_text().strip():
        raise RuntimeError("standalone bundle has no generated.sql")
    script = generated.read_text()
    if RUNTIME_REFERENCE.search(script):
        raise RuntimeError("standalone SQL contains a runtime jsontailor reference")
    header = BUNDLE_HEADER.match(script)
    if header is None:
        raise RuntimeError("standalone SQL lacks the generator/version/target/hash header")
    version, major, encoded_view, generator_hash = header.groups()
    verify_generator(version, int(major), generator_hash)
    try:
        view = json.loads(encoded_view)
    except ValueError as error:
        raise RuntimeError("standalone SQL has an invalid target-view header") from error
    preamble = bundle_preamble(version, int(major), view, notice.decode('utf-8'), generator_hash)
    if not script.startswith(preamble):
        raise RuntimeError("standalone SQL lacks the complete embedded notice, transaction, version guard or pinned search_path")
    if not script.endswith('\nCOMMIT;\n') or not script[len(preamble):-len('\nCOMMIT;\n')].strip():
        raise RuntimeError("standalone SQL lacks its body or final transaction commit")


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
