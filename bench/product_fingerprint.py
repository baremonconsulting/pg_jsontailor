"""Shared, versioned benchmark product fingerprint policy."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import stat

SCHEMA_VERSION = 2
PRODUCT_INPUTS = ("sql", "pg_jsontailor.control", "Makefile")
_DECLARATIONS = (
    (b"REGRESS", re.compile(rb"^REGRESS[ \t]*=[ \t]*[A-Za-z0-9_./ -]*$")),
    (b"REGRESS_OPTS", re.compile(rb"^REGRESS_OPTS[ \t]*=[ \t]*[A-Za-z0-9_./= -]*$")),
    (b"ISOLATION", re.compile(rb"^ISOLATION[ \t]*=[ \t]*[A-Za-z0-9_./ -]*$")),
    (b"ISOLATION_OPTS", re.compile(rb"^ISOLATION_OPTS[ \t]*=[ \t]*[A-Za-z0-9_./= -]*$")),
)
_CANONICAL_FIXED = {
    0: b"EXTENSION = pg_jsontailor",
    1: b"DATA = sql/pg_jsontailor--0.2.0.sql",
    2: b"",
    7: b"",
    8: b"PG_CONFIG ?= pg_config",
    9: b"PGXS := $(shell $(PG_CONFIG) --pgxs)",
    10: b"include $(PGXS)",
    11: b"",
    12: b"pg_regress_clean_files = tests/output/ tests/output_iso/",
    13: b"",
    14: b"",
}


def _normalized_makefile(content: bytes) -> bytes | None:
    lines = content.split(b"\n")
    if len(lines) != 15 or any(lines[index] != value
                               for index, value in _CANONICAL_FIXED.items()):
        return None
    for index, (name, pattern) in enumerate(_DECLARATIONS, start=3):
        if pattern.fullmatch(lines[index]) is None:
            return None
        lines[index] = name + b" = <test-only>"
    return b"\n".join(lines)


def digest(path: str, content: bytes, mode: str = "100644",
           schema: int = SCHEMA_VERSION) -> str:
    if schema == 1:
        return hashlib.sha256(content).hexdigest()
    if schema != SCHEMA_VERSION:
        raise ValueError("unsupported product fingerprint schema")
    if mode not in {"100644", "100755"}:
        raise ValueError(f"non-regular product input mode: {mode}")
    normalized = _normalized_makefile(content) if path == "Makefile" else None
    domain = b"normalized-makefile" if normalized is not None else b"raw-product"
    payload = normalized if normalized is not None else content
    return hashlib.sha256(b"pgjt-fingerprint-v2\0" + domain + b"\0" +
                          mode.encode() + b"\0" + payload).hexdigest()


def working_tree_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for relative in PRODUCT_INPUTS:
        source = root / relative
        files = sorted((p for p in source.rglob("*")
                        if p.is_file() or p.is_symlink())) if source.is_dir() else [source]
        for path in files:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError(f"non-regular product input: {path}")
            mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
            name = path.relative_to(root).as_posix()
            hashes[name] = digest(name, path.read_bytes(), mode)
    return hashes
