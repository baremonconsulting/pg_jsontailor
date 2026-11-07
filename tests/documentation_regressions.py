#!/usr/bin/env python3
"""Negative checks for public documentation and redistributed SQL notices."""
from __future__ import annotations

import io
from pathlib import Path
import re
import shutil
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_generated_sql_notice import verify_archive, verify_bundle, verify_notice, verify_source
from verify_standalone_results import EXPECTED_IDS, verify as verify_results


def claims() -> None:
    readme = (ROOT / "README.md").read_text()
    fixture = (ROOT / "examples/shop_schema.sql").read_text()
    assert readme.index("Warning: use a disposable database only") < readme.index("examples/shop_schema.sql")
    assert fixture.index("DISPOSABLE DATABASE ONLY") < fixture.index("DROP SCHEMA")
    assert "examples/orders_dv.spec.json" in readme
    semantics = (ROOT / "docs/SEMANTICS.md").read_text()
    api = (ROOT / "docs/API.md").read_text()
    upgrading = (ROOT / "docs/UPGRADING.md").read_text()
    for marker in ("first 128 bits of SHA-256", "32 uppercase hexadecimal",
                   "not an authentication token", "object.etag=false", "object key",
                   "visibility-relative", "Hidden child", "DELETE 0",
                   "triggers cannot inspect"):
        assert marker in semantics, f"missing precise public claim: {marker}"
    for marker in ("Generated columns are not supported", "root.fields", "parent_fk",
                   "child key", "cannot require or inspect a client-supplied predicate"):
        assert marker in api, f"missing mapping/deletion restriction: {marker}"
    assert "DROP FUNCTION api.orders_dv__delete_document(bigint, text);" in upgrading
    assert "p.proname = ANY" not in upgrading
    for path in ("README.md", "docs/API.md", "docs/SEMANTICS.md", "docs/UPGRADING.md",
                 "SECURITY.md", "SUPPORT.md"):
        content = (ROOT / path).read_text()
        assert "NOCHECK" not in content, f"misleading shorthand: {path}"
        assert not re.search(r"\b(?:normaliz\w*|stabiliz\w*|behavior\w*|synchroniz\w*)\b",
                             content, re.IGNORECASE), f"non-British public prose: {path}"


def rejected(callback) -> None:
    try:
        callback()
    except (RuntimeError, FileNotFoundError, KeyError):
        return
    raise AssertionError("invalid generated-SQL package was accepted")


def notices() -> None:
    licence = (ROOT / "LICENSE").read_bytes()
    notice = (ROOT / "GENERATED_SQL_NOTICE").read_bytes()
    verify_notice(notice, licence)
    verify_source(ROOT)
    # Copyright, permission and both disclaimers are independently mandatory.
    for fragment in (b"Copyright (c) 2026, Baremon s.r.o.", b"Permission to use, copy, modify, and distribute",
                     b"IN NO EVENT SHALL THE COPYRIGHT HOLDERS", b"THE COPYRIGHT HOLDERS SPECIFICALLY DISCLAIM"):
        rejected(lambda: verify_notice(notice.replace(fragment, b""), licence))
    rejected(lambda: verify_notice(b"PostgreSQL License\n", licence))
    with tempfile.TemporaryDirectory(prefix="pgjt72-negative-") as temporary:
        folder = Path(temporary)
        (folder / "generated.sql").write_text("SELECT 1;\n")
        rejected(lambda: verify_bundle(folder))
        shutil.copyfile(ROOT / "GENERATED_SQL_NOTICE", folder / "GENERATED_SQL_NOTICE")
        verify_bundle(folder)
        (folder / "generated.sql").write_text("SELECT jsontailor.duality_view_inventory();\n")
        rejected(lambda: verify_bundle(folder))
        for omitted in (None, "GENERATED_SQL_NOTICE", "LICENSE", "examples/standalone/README.md"):
            archive_path = folder / "source.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name in ("LICENSE", "GENERATED_SQL_NOTICE", "examples/standalone/README.md"):
                    if name != omitted:
                        data = (ROOT / name).read_bytes()
                        member = tarfile.TarInfo("pg_jsontailor-0.2.0/" + name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
            if omitted is None:
                verify_archive(archive_path)
            else:
                rejected(lambda: verify_archive(archive_path))


def standalone_attestation() -> None:
    with tempfile.TemporaryDirectory(prefix="pgjt68-negatives-") as temporary:
        folder = Path(temporary)
        paths = {}
        import json
        for major in ("17", "18", "19"):
            rows = [{"name": name, "status": "PASS", "postgresql": major,
                     "source_commit": "a" * 40, "dirty_tree": False,
                     "generated_sql_sha256": "b" * 64} for name in sorted(EXPECTED_IDS)]
            path = folder / (major + ".jsonl")
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            paths[major] = path
        verify_results(paths, "a" * 40)
        rejected(lambda: verify_results({"17": paths["17"], "18": paths["18"]}, "a" * 40))
        rejected(lambda: verify_results(paths, "c" * 40))
        original = paths["17"].read_text()
        rejected_rows = [original.splitlines()[1:], original.splitlines() + [original.splitlines()[0]],
                         original.replace('"PASS"', '"FAIL"').splitlines(),
                         original.replace('"dirty_tree": false', '"dirty_tree": true').splitlines(),
                         original.replace('"postgresql": "17"', '"postgresql": "18"').splitlines()]
        for mutation in rejected_rows:
            paths["17"].write_text("\n".join(mutation) + "\n")
            rejected(lambda: verify_results(paths, "a" * 40))
        paths["17"].write_text(original)


if __name__ == "__main__":
    claims()
    notices()
    standalone_attestation()
    print("PGJT-67/documentation-claims: PASS")
    print("PGJT-72/notice-packaging-negatives: PASS")
    print("PGJT-68/attestation-negatives: PASS")
