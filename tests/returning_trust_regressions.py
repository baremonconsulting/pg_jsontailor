"""RETURNING command tags and stale/invalid documents must never pass."""
import copy
import json
from subprocess import CompletedProcess
import unittest
from psql_results import tuples, document


class ReturningEvidence(unittest.TestCase):
    def test_command_tags_are_not_tuples(self):
        for text in ["UPDATE 1\n", "x\ny\nUPDATE 1\n", "x\nUPDATE 0\n", "x\n"]:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                tuples(CompletedProcess([], 0, text, ""), "UPDATE", 1)
        self.assertEqual(tuples(CompletedProcess([], 0, "{\"x\":1}\nUPDATE 1\n", ""), "UPDATE", 1),
                         [['{"x":1}']])

    def test_stale_invalid_or_unchanged_tokens_fail(self):
        stored = {"_id": 1, "label": "new", "_metadata": {"etag": "A" * 32}}
        document(json.dumps(stored), stored)
        for change in [lambda row: row.update(label="stale"),
                       lambda row: row["_metadata"].update(etag="a" * 32),
                       lambda row: row["_metadata"].update(etag="A" * 31),
                       lambda row: row.pop("_metadata")]:
            returned = copy.deepcopy(stored); change(returned)
            with self.assertRaises(RuntimeError):
                document(json.dumps(returned), stored)
        with self.assertRaisesRegex(RuntimeError, "did not change"):
            document(stored, stored, stored)


if __name__ == "__main__":
    unittest.main()
