"""Versioned, deterministic batching rules for benchmark evidence."""
from __future__ import annotations

import math
import statistics

PROTOCOL_VERSION = 1
BATCH_SIZES = (1, 2, 4, 8, 16, 32)
CALIBRATION_BATCHES = 3
TARGET_BATCH_MS = 1.0
PHASES = ("calibration", "warmup", "measured")


def require_integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise RuntimeError(f"invalid {name}")
    return value


def arm_order(batch, constituent):
    """Alternate the first arm for every constituent pair and outer batch."""
    require_integer(batch, "batch")
    require_integer(constituent, "constituent")
    return ("direct", "duality") if (batch + constituent) % 2 == 0 else ("duality", "direct")


def evidence_key(session, size, operation, implementation, phase, level, batch, constituent):
    for value, name in [(session, "session"), (size, "size"), (level, "level"),
                        (batch, "batch"), (constituent, "constituent")]:
        require_integer(value, name)
    if phase not in PHASES or implementation not in {"direct", "duality"}:
        raise RuntimeError("invalid evidence coordinate")
    return f"{session}:{size}:{operation}:{implementation}:{phase}:{level}:{batch}:{constituent}"


def first_qualifying_level(totals):
    """Return the first level where both three-pilot arm medians reach the target."""
    if not isinstance(totals, dict):
        raise RuntimeError("invalid calibration inventory")
    for level in BATCH_SIZES:
        if level not in totals:
            raise RuntimeError("calibration level missing")
        arms = totals[level]
        if not isinstance(arms, dict) or set(arms) != {"direct", "duality"}:
            raise RuntimeError("calibration arm inventory differs")
        for values in arms.values():
            if (not isinstance(values, list) or len(values) != CALIBRATION_BATCHES
                    or any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0
                           for v in values)):
                raise RuntimeError("invalid calibration timing")
        if level_qualifies(arms):
            return level
    raise RuntimeError("calibration batch-size cap exhausted")


def level_qualifies(arms):
    if not isinstance(arms, dict) or set(arms) != {"direct", "duality"}:
        raise RuntimeError("calibration arm inventory differs")
    for values in arms.values():
        if (not isinstance(values, list) or len(values) != CALIBRATION_BATCHES
                or any(type(v) not in {int, float} or not math.isfinite(v) or v <= 0
                       for v in values)):
            raise RuntimeError("invalid calibration timing")
    return min(statistics.median(arms[arm]) for arm in arms) >= TARGET_BATCH_MS
