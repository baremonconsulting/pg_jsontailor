"""Strict psql RETURNING tuples, distinct from the trailing command tag."""
from __future__ import annotations

import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import loads


def tuples(result, command: str, count: int, columns: int = 1):
    if result.returncode:
        raise RuntimeError(f"RETURNING statement failed: {result.stderr}")
    lines = result.stdout.splitlines()
    tag = f"INSERT 0 {count}" if command == "INSERT" else f"{command} {count}"
    if not lines or lines[-1] != tag or len(lines) != count + 1:
        raise RuntimeError(f"expected exactly {count} RETURNING tuples and {tag}: {lines!r}")
    rows = [line.split("|", columns - 1) for line in lines[:-1]]
    if any(len(row) != columns for row in rows):
        raise RuntimeError("incorrect RETURNING column count")
    return rows


def document(value, stored, previous=None):
    returned = loads(value) if isinstance(value, str) else value
    if not isinstance(returned, dict) or not isinstance(returned.get("_metadata"), dict):
        raise RuntimeError("RETURNING document shape is invalid")
    etag = returned["_metadata"].get("etag")
    if not isinstance(etag, str) or re.fullmatch(r"[0-9A-F]{32}", etag) is None:
        raise RuntimeError("RETURNING ETag must contain 32 uppercase hexadecimal characters")
    if returned != stored:
        raise RuntimeError("RETURNING document differs from fresh read-back")
    if previous is not None and etag == previous["_metadata"]["etag"]:
        raise RuntimeError("RETURNING ETag did not change after covered state changed")
    return returned
