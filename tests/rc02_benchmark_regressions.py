"""Focused benchmark fixture and example-contract regressions."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench.fixture_health import CHILD_INDEX_SQL, assert_fixture_health


class BenchmarkFixture(unittest.TestCase):
    def test_pgjt_85_health_gate_is_fail_closed(self):
        calls = []
        assert_fixture_health(lambda query: calls.append(query) or '{"status":"OK"}')
        self.assertEqual(['SELECT bench_dv.orders_dv__check();'], calls)
        for response in ('{}', '[]', '{"status":"FAIL"}'):
            with self.subTest(response=response), self.assertRaisesRegex(RuntimeError, 'health check failed'):
                assert_fixture_health(lambda _: response)

    def test_pgjt_85_required_composite_index_and_gate_are_used(self):
        self.assertEqual('CREATE INDEX ON bench_dv.items(order_id,item_id);', CHILD_INDEX_SQL)
        producer = (ROOT / 'bench/duality_overhead.py').read_text()
        self.assertIn('sql(CHILD_INDEX_SQL)', producer)
        self.assertIn('assert_fixture_health(lambda statement: sql(statement, tuples=True))', producer)

    def test_pgjt_88_documented_stage_requires_true_identity_agreement(self):
        self.assertIn("CHECK ((doc->'_id' = to_jsonb(root_id)) IS TRUE)",
                      (ROOT / 'docs/MERGE.md').read_text())


if __name__ == '__main__':
    unittest.main()
