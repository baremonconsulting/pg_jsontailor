#!/usr/bin/env python3
"""Attest the exact standalone evidence inventory across PostgreSQL majors."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

# Reviewed independently of the emitter. An emitter cannot shrink its own gate.
EXPECTED_IDS = frozenset({
    "PGJT-68/export-candidate", "PGJT-68/clean-runtime", "PGJT-68/crud-etags",
    "PGJT-68/direct-rpc-delete", "PGJT-68/merge", "PGJT-68/health-binding-guards",
    "PGJT-68/rls-acl", "PGJT-68/custom-restore", "PGJT-68/plain-restore",
    "PGJT-114/self-protecting-header", "PGJT-114/wrong-major",
    "PGJT-114/hostile-search-path", "PGJT-114/transaction-invocations",
    "PGJT-114/bundle-negatives",
})
EXPECTED_MAJORS = frozenset({"17", "18", "19"})


def unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("duplicate JSON key: " + key)
        result[key] = value
    return result


def verify(paths: dict[str, Path], source_commit: str, allow_dirty: bool = False,
           archive_sha256: str | None = None) -> dict:
    if set(paths) != EXPECTED_MAJORS:
        raise RuntimeError("standalone evidence requires exactly PostgreSQL 17, 18 and 19")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise RuntimeError("standalone source commit must be a full SHA")
    if archive_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", archive_sha256) is None:
        raise RuntimeError("expected archive checksum must be SHA-256")
    artifacts = []
    origins = set()
    archive_manifests = set()
    for major, path in sorted(paths.items()):
        rows = [json.loads(line, object_pairs_hook=unique) for line in path.read_text().splitlines()
                if line.strip()]
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("standalone results must be non-empty object rows")
        if len(rows) != len(EXPECTED_IDS) or {row.get("name") for row in rows} != EXPECTED_IDS:
            raise RuntimeError("standalone result inventory differs from the reviewed contract")
        hashes = set()
        for row in rows:
            if row.get("status") != "PASS" or row.get("postgresql") != major:
                raise RuntimeError("standalone result status or PostgreSQL major differs")
            if row.get("source_commit") != source_commit:
                raise RuntimeError("standalone result is not bound to the candidate SHA")
            if row.get("source_provenance") not in {"git-checkout", "archive-declared"}:
                raise RuntimeError("standalone source provenance is missing or unknown")
            origins.add(row["source_provenance"])
            if row["source_provenance"] == "archive-declared":
                if archive_sha256 is None or row.get("source_archive_sha256") != archive_sha256:
                    raise RuntimeError("archive evidence differs from the independently supplied checksum")
                for field in ("source_archive_sha256", "source_manifest_sha256"):
                    if not isinstance(row.get(field), str) or re.fullmatch(r"[0-9a-f]{64}", row[field]) is None:
                        raise RuntimeError("archive-declared evidence requires verified archive and manifest digests")
                archive_manifests.add(row["source_manifest_sha256"])
            elif archive_sha256 is not None:
                raise RuntimeError("archive rehearsal cannot substitute checkout evidence")
            if not isinstance(row.get("dirty_tree"), bool) or (row["dirty_tree"] and not allow_dirty):
                raise RuntimeError("dirty standalone evidence is diagnostic only")
            value = row.get("generated_sql_sha256")
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise RuntimeError("standalone generated-SQL digest is missing or malformed")
            hashes.add(value)
        if len(hashes) != 1:
            raise RuntimeError("standalone results disagree on exported SQL identity")
        artifacts.append({"postgresql": major, "results_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "generated_sql_sha256": next(iter(hashes)), "result_count": len(rows)})
    if len(origins) != 1 or len(archive_manifests) > 1:
        raise RuntimeError("standalone matrix mixes source provenance or extracted manifests")
    return {"schema_version": 1, "source_commit": source_commit,
            "source_provenance": next(iter(origins)), "source_archive_sha256": archive_sha256,
            "source_manifest_sha256": next(iter(archive_manifests), None),
            "diagnostic_only": allow_dirty, "standalone": artifacts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", action="append", required=True, metavar="MAJOR=PATH")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--archive-sha256", help="approved checksum; mandatory for archive-declared results")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="diagnostic use only; never release evidence")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    paths = {}
    for declaration in args.result:
        major, path = declaration.split("=", 1)
        if major in paths:
            raise RuntimeError("duplicate standalone major")
        paths[major] = Path(path)
    result = verify(paths, args.source_commit, args.allow_dirty, args.archive_sha256)
    if args.output:
        args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("standalone PostgreSQL matrix attestation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
