"""Adversarial regression fixtures for complete, unambiguous suite evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.strict_results import inventories, loads, result_rows, validate


class StrictEvidence(unittest.TestCase):
    def test_duplicate_keys_and_non_json_constants(self):
        for value in ['{"status":"FAIL","status":"PASS"}', '{"nested":{"x":1,"x":2}}',
                      '{"value":NaN}', '{"value":Infinity}']:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                loads(value)

    def test_non_object_and_malformed_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "results.jsonl"
            for content in ['[]', '1', 'null', '{}\ntrue\n', '{}\n{broken\n', '']:
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises((RuntimeError, ValueError)):
                    result_rows(path)

    def test_every_reviewed_inventory_rejects_truncation_extra_duplicate_and_failure(self):
        for suite, ids in inventories().items():
            rows = [{"id": ident, "status": "PASS"} for ident in ids]
            validate(rows, suite)
            failing = copy.deepcopy(rows); failing[-1]["status"] = "FAIL"
            cases = [rows[:-1], rows + [{"id": "unreviewed", "status": "PASS"}],
                     rows + [rows[0]], failing,
                     rows + [{"id": "unreferenced-failure", "status": "FAIL"}]]
            for changed in cases:
                with self.subTest(suite=suite, rows=changed), self.assertRaises(RuntimeError):
                    validate(changed, suite)


if __name__ == "__main__":
    unittest.main()
