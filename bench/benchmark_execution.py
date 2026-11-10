"""Execution primitive shared by the benchmark producer and fault regressions."""
from __future__ import annotations

import json


def explain(session, statements, observer=None, before_statement=None):
    payloads = []
    for index, statement in enumerate(statements):
        if before_statement is not None:
            before_statement(index, statement)
        raw = session.execute(
            "EXPLAIN (ANALYZE, TIMING OFF, BUFFERS, WAL, FORMAT JSON) " + statement + ";")
        try:
            payload = json.loads(raw)[0]
        except (json.JSONDecodeError, IndexError, TypeError) as error:
            raise RuntimeError(f"invalid plan for statement {index}: {raw[:500]}") from error
        payloads.append(payload)
        if observer is not None:
            observer(index, statement, payload)
    return {
        "execution_ms": sum(float(p["Execution Time"]) for p in payloads),
        "planning_ms": sum(float(p["Planning Time"]) for p in payloads),
        "shared_hit_blocks": sum(int(p["Plan"].get("Shared Hit Blocks", 0)) for p in payloads),
        "shared_dirtied_blocks": sum(int(p["Plan"].get("Shared Dirtied Blocks", 0)) for p in payloads),
        "wal_records": sum(int(p["Plan"].get("WAL Records", 0)) for p in payloads),
        "plans": payloads,
    }
