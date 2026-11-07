"""Mutation witnesses must be exact and retain reviewed runtime capabilities."""
from pathlib import Path
import unittest
from mutate_generated_sql import exact_witness


class ExactMutationWitness(unittest.TestCase):
    def test_rls_probe_requires_reviewed_array_reader_capability(self):
        source = (Path(__file__).with_name('mutate_generated_sql.py')).read_text()
        self.assertIn("has_function_privilege('jt50_limited','shop.orders_dv__bounded_array(bigint)'", source)
        self.assertIn("jsontailor.grant_array_reader('shop.orders_dv','jt50_limited',false)", source)
        self.assertLess(source.index('fresh mutation role unexpectedly has array-reader capability'),
                        source.index("jsontailor.grant_array_reader('shop.orders_dv','jt50_limited',false)"))

    def test_expected_lock_and_privilege_witnesses_count(self):
        killed, witnesses = exact_witness('locks', [('security_contract_matrix.py',
            ['PGJT-46/object-etag-lock-matrix', 'PGJT-46/membership-column-grants'])])
        self.assertTrue(killed)
        self.assertEqual(len(witnesses), 2)

    def test_unrelated_failure_is_harness_failure_even_alongside_expected(self):
        with self.assertRaisesRegex(RuntimeError, 'outside its expected witness'):
            exact_witness('locks', [('security_contract_matrix.py',
                ['PGJT-46/object-etag-lock-matrix', 'PGJT-46/root-rls-matrix'])])

    def test_no_failure_is_a_survivor(self):
        self.assertEqual(exact_witness('atomic-reconciliation', [('validation_security.py', [])]),
                         (False, []))


if __name__ == '__main__':
    unittest.main()
