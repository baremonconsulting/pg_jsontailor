#!/usr/bin/env python3
"""Crash-safe, non-publishable benchmark diagnostics."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from typing import Any

from bench.installation import canonical, sha256

JOURNAL = ".diagnostic-events.jsonl"
MANIFEST = ".diagnostic-manifest.json"
SCHEMA_VERSION = 1


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".new")
    with temporary.open("wb") as stream:
        stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)


class DiagnosticJournal:
    """Durably retain the completed prefix without changing benchmark measurements."""

    def __init__(self, folder: Path, provenance: dict[str, Any]):
        self.folder = folder
        self.path = folder / JOURNAL
        self.manifest = folder / MANIFEST
        self.sequence = 0
        self.previous = "0" * 64
        self.journal_hash = hashlib.sha256()
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.path.exists() or self.manifest.exists():
            raise RuntimeError("diagnostic evidence already exists")
        self.append("header", provenance)

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        body = {"sequence": self.sequence, "previous": self.previous,
                "kind": kind, "payload": payload}
        digest = sha256(canonical(body))
        event = {**body, "digest": digest}
        encoded = canonical(event) + b"\n"
        with self.path.open("ab", buffering=0) as stream:
            stream.write(encoded)
            os.fsync(stream.fileno())
        self.journal_hash.update(encoded)
        self.sequence += 1
        self.previous = digest
        self._manifest("INCOMPLETE/UNFINALISED")

    def _manifest(self, status: str) -> None:
        _atomic_json(self.manifest, {
            "schema_version": SCHEMA_VERSION,
            "purpose": "diagnostic-only; never publishable benchmark evidence",
            "status": status,
            "events": self.sequence,
            "last_event": self.previous,
            "journal_sha256": self.journal_hash.hexdigest(),
        })

    def fail(self, phase: str, error: BaseException, restoration: str,
             interrupted: dict[str, Any] | None = None) -> None:
        self.append("terminal-failure", {
            "phase": phase,
            "error_type": type(error).__name__,
            "error": str(error),
            "restoration": restoration,
            "interrupted": interrupted,
        })
        self._manifest("FAIL")

    def discard_after_success(self) -> None:
        for path in (self.path, self.manifest):
            path.unlink()
        _sync_directory(self.folder)


def verify_diagnostic(folder: Path) -> dict[str, Any]:
    """Classify diagnostic evidence as terminal fail, incomplete prefix or corrupt."""
    folder = folder.resolve()
    manifest_path, journal_path = folder / MANIFEST, folder / JOURNAL
    if not journal_path.is_file():
        raise RuntimeError("diagnostic journal is absent")
    try:
        lines = journal_path.read_bytes().splitlines()
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("corrupt diagnostic journal") from error
    if not events:
        raise RuntimeError("empty diagnostic journal")
    previous = "0" * 64
    for sequence, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != {
                "sequence", "previous", "kind", "payload", "digest"}:
            raise RuntimeError("invalid diagnostic event schema")
        body = {name: event[name] for name in ["sequence", "previous", "kind", "payload"]}
        if event["sequence"] != sequence or event["previous"] != previous:
            raise RuntimeError("diagnostic sequence or chain differs")
        if sha256(canonical(body)) != event["digest"]:
            raise RuntimeError("diagnostic event digest differs")
        previous = event["digest"]
    header = events[0]
    required = {"source_commit", "source_dirty", "installation_sha256", "installation_log_sha256",
                "workload_sha256", "producer_sha256", "command", "started_at"}
    if header["kind"] != "header" or not isinstance(header["payload"], dict) \
            or set(header["payload"]) != required or header["payload"]["source_dirty"] is not False:
        raise RuntimeError("invalid diagnostic provenance")
    if not manifest_path.is_file():
        return {"outcome": "INCOMPLETE_PREFIX", "events": len(events), "publishable": False,
                "restoration_verified": False}
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("corrupt diagnostic manifest") from error
    expected = {"schema_version", "purpose", "status", "events", "last_event", "journal_sha256"}
    if not isinstance(manifest, dict) or set(manifest) != expected or manifest["schema_version"] != 1 \
            or manifest["purpose"] != "diagnostic-only; never publishable benchmark evidence" \
            or type(manifest["events"]) is not int or not 0 < manifest["events"] <= len(events):
        raise RuntimeError("diagnostic manifest binding differs")
    bound_count = manifest["events"]
    bound_bytes = b"\n".join(lines[:bound_count]) + b"\n"
    if manifest["last_event"] != events[bound_count - 1]["digest"] \
            or manifest["journal_sha256"] != sha256(bound_bytes):
        raise RuntimeError("diagnostic manifest binding differs")
    if bound_count < len(events):
        return {"outcome": "INCOMPLETE_PREFIX", "events": len(events), "publishable": False,
                "restoration_verified": False}
    if manifest["status"] == "INCOMPLETE/UNFINALISED":
        return {"outcome": "INCOMPLETE_PREFIX", "events": len(events), "publishable": False,
                "restoration_verified": False}
    if manifest["status"] != "FAIL" or events[-1]["kind"] != "terminal-failure":
        raise RuntimeError("invalid diagnostic terminal state")
    restoration = events[-1]["payload"].get("restoration")
    if restoration not in {"verified", "failed", "not-reached"}:
        raise RuntimeError("invalid restoration result")
    return {"outcome": "TERMINAL_FAIL", "events": len(events), "publishable": False,
            "restoration_verified": restoration == "verified"}
