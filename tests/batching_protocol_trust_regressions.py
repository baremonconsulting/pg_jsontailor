"""PGJT-78 adversarial checks for the versioned batching protocol."""
from __future__ import annotations

import unittest

from bench.batching_protocol import (BATCH_SIZES, arm_order, evidence_key,
                                      first_qualifying_level)


class BatchingProtocolTrust(unittest.TestCase):
    def arms(self, value):
        return {"direct": [value] * 3, "duality": [value] * 3}

    def test_first_shared_qualifying_level_is_selected(self):
        totals = {1: self.arms(.4), 2: self.arms(.8), 4: self.arms(1.6)}
        self.assertEqual(first_qualifying_level(totals), 4)

    def test_skipped_level_and_cap_exhaustion_fail(self):
        with self.assertRaisesRegex(RuntimeError, "level missing"):
            first_qualifying_level({1: self.arms(.4), 4: self.arms(1.6)})
        with self.assertRaisesRegex(RuntimeError, "cap exhausted"):
            first_qualifying_level({level: self.arms(.5) for level in BATCH_SIZES})

    def test_asymmetric_invalid_and_boolean_timings_fail(self):
        for value in [0, -1, float("nan"), True]:
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "timing"):
                first_qualifying_level({1: self.arms(value)})
        with self.assertRaisesRegex(RuntimeError, "arm inventory"):
            first_qualifying_level({1: {"direct": [1.0] * 3}})
        with self.assertRaisesRegex(RuntimeError, "timing"):
            first_qualifying_level({1: {"direct": [1.0] * 3, "duality": [1.0] * 2}})

    def test_order_and_coordinate_identity_are_deterministic(self):
        self.assertEqual(arm_order(0, 0), ("direct", "duality"))
        self.assertEqual(arm_order(0, 1), ("duality", "direct"))
        self.assertEqual(arm_order(1, 0), ("duality", "direct"))
        self.assertNotEqual(
            evidence_key(0, 0, "read", "direct", "calibration", 1, 0, 0),
            evidence_key(0, 0, "read", "direct", "calibration", 2, 0, 0),
        )


if __name__ == "__main__":
    unittest.main()
