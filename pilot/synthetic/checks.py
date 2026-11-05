"""Pure acceptance checks shared by the pilot runner and regressions."""
from __future__ import annotations

import statistics
from typing import Callable


def operational_read_sample(http: Callable, count: int = 20) -> tuple[bool, dict]:
    samples = []
    for index in range(count):
        status, rows, elapsed = http(
            f"operational read {index + 1}", query="?_id=eq.201")
        data = rows[0].get("data") if isinstance(rows, list) and len(rows) == 1 else None
        valid = (status == 200 and isinstance(rows, list) and len(rows) == 1
                 and rows[0].get("_id") == 201 and isinstance(data, dict)
                 and data.get("_id") == 201 and isinstance(data.get("_metadata"), dict)
                 and isinstance(data["_metadata"].get("etag"), str)
                 and bool(data["_metadata"]["etag"]))
        samples.append({"status": status, "valid": valid, "elapsed_ms": elapsed})
    observed = {
        "statuses": [sample["status"] for sample in samples],
        "valid_documents": sum(sample["valid"] for sample in samples),
        "median_ms": round(statistics.median(sample["elapsed_ms"] for sample in samples), 3),
        "max_ms": round(max(sample["elapsed_ms"] for sample in samples), 3),
    }
    passed = all(sample["valid"] and sample["elapsed_ms"] < 1000 for sample in samples)
    return passed, observed
