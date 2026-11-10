#!/usr/bin/env python3
"""Unit checks for the PGJT-111/112 generated-DDL lint and matrix harness."""
from __future__ import annotations

from itertools import combinations
import subprocess
import unittest

from mapped_type_matrix import (
    EXPECTED_FIELD_NAMES, MIXED_INVALID_VALUES, MIXED_OPERATIONS,
    lint_mixed_type_ddl, mixed_document, mixed_invalid_matrix,
    mixed_rows, mixed_script, mixed_statement, mixed_type_matrix,
)


def generated_ddl() -> str:
    loop = """    FOR bad,keyj IN SELECT key,value FROM jsonb_each(cfg#>'{root,fields}') LOOP
      IF doc?bad THEN
        IF NOT pg_input_is_valid(doc->>bad,concat(sql_type)) THEN RAISE EXCEPTION 'invalid';END IF;
      END IF;
    END LOOP;"""
    # The generated functions contain SQL-quoted bodies, not raw PL/pgSQL.
    projection = ("FOR bad,keyj IN SELECT key,value FROM jsonb_each(cfg#>'{root,fields}') "
                  "LOOP IF doc?bad THEN NULL; END IF; END LOOP;\n")
    body = ("IF NOT pg_input_is_valid(doc->>'_id',sql_type) THEN NULL; END IF;\n"
            + loop + "\n" + projection + loop + "\n" + projection).replace("'", "''")
    return "\n".join(f"CREATE FUNCTION example.fn{index}() RETURNS trigger "
                     f"LANGUAGE plpgsql AS '{body}';" for index in range(3))


class FakeDatabase:
    def __init__(self, *, sentinel=True):
        self.scripts = []
        self.sentinel = sentinel

    def run(self, sql, check=True):
        self.scripts.append(sql)
        output = (generated_ddl() if "SELECT jsontailor.duality_view_ddl" in sql else
                  "PGJT-MIXED-OK\n" if self.sentinel else "")
        return subprocess.CompletedProcess(["psql"], 0, output, "")


class MixedTypeHarnessTests(unittest.TestCase):
    def test_lint_excludes_valid_single_type_key_checks(self):
        lint_mixed_type_ddl(generated_ddl())

    def test_lint_rejects_each_mutated_root_callsite(self):
        token = "concat(sql_type)"
        segments = generated_ddl().split(token)
        self.assertEqual(7, len(segments))
        for index in range(6):
            modified = "".join(segment + ("sql_type" if position == index else token)
                               for position, segment in enumerate(segments[:-1])) + segments[-1]
            with self.subTest(callsite=index), self.assertRaises(ValueError):
                lint_mixed_type_ddl(modified)

    def test_lint_fails_closed_on_missing_loop_or_comment_only_call(self):
        with self.assertRaises(ValueError):
            lint_mixed_type_ddl(generated_ddl().replace("FOR bad,keyj", "FOR other,keyj", 1))
        with self.assertRaises(ValueError):
            lint_mixed_type_ddl(generated_ddl().replace(
                "IF NOT pg_input_is_valid(doc->>bad", "-- IF NOT pg_input_is_valid(doc->>bad", 1))

    def test_full_document_uses_all_types_and_preserves_the_referenced_object(self):
        original = mixed_document(EXPECTED_FIELD_NAMES, 100000)
        changed = mixed_document(EXPECTED_FIELD_NAMES, 100000, True)
        self.assertEqual(original["detail"], changed["detail"])
        self.assertEqual("2026-01-01T01:02:03.123456", original["detail"]["timestampValue"])
        self.assertEqual(12, len(EXPECTED_FIELD_NAMES))
        for field in EXPECTED_FIELD_NAMES:
            self.assertIsNotNone(original[field])
            self.assertIsNotNone(original["detail"][field])
            self.assertIsNotNone(original["items"][0][field])
            self.assertNotEqual(original[field], changed[field])
        self.assertEqual(110000, mixed_rows(original)["root"]["detail_id"])

    def test_pair_documents_do_not_accidentally_warm_up_with_another_type(self):
        for pair in combinations(EXPECTED_FIELD_NAMES, 2):
            value = mixed_document(pair, 100000)
            for target in (value, value["detail"], value["items"][0]):
                self.assertEqual(set(pair), {name for name in EXPECTED_FIELD_NAMES
                                            if target[name] is not None})

    def test_session_modes_have_one_transaction_and_real_multirow_statements(self):
        names = ("int2Value", "textValue")
        for operation in MIXED_OPERATIONS:
            for mode in ("isolated", "transaction", "multirow"):
                script = mixed_script(operation, names, mode)
                self.assertEqual(1, script.count("BEGIN; SET LOCAL"))
                self.assertEqual(1, script.count("ROLLBACK;"))
                self.assertEqual(1, script.count("SELECT 'PGJT-MIXED-OK'"))
                marker = {"insert": "INSERT INTO jt_type_matrix.documents",
                          "replacement": "UPDATE jt_type_matrix.documents",
                          "merge_insert": "MERGE INTO jt_type_matrix.documents",
                          "merge_update": "MERGE INTO jt_type_matrix.documents"}[operation]
                self.assertEqual(3 if mode == "transaction" else 1, script.count(marker))
                if operation in {"replacement", "merge_update"}:
                    self.assertIn("CREATE TEMP TABLE pgjt_mixed_before", script)
                    self.assertIn("<>b.etag", script)
        self.assertNotEqual(mixed_script("insert", names, "transaction"),
                            mixed_script("insert", names, "transaction", True))

    def test_matrix_executes_the_complete_inventory(self):
        db = FakeDatabase()
        rows = []
        mixed_type_matrix(db, lambda *row: rows.append(row))
        self.assertEqual(1073, len(db.scripts))  # DDL + 16 full + 264/528/264 pair cases.
        self.assertEqual(5, len(rows))
        self.assertTrue(all(row[1] for row in rows))
        self.assertIn("executions=16/16", rows[1][2])
        for row, count in zip(rows[2:], (264, 528, 264)):
            self.assertIn("cases=66", row[2])
            self.assertIn(f"executions={count}/{count}", row[2])

    def test_zero_exit_without_the_completion_witness_is_not_a_pass(self):
        rows = []
        mixed_type_matrix(FakeDatabase(sentinel=False), lambda *row: rows.append(row))
        self.assertTrue(rows[0][1])
        self.assertTrue(all(not row[1] for row in rows[1:]))

    def test_invalid_matrix_requires_pt422_and_relational_rollback(self):
        db = FakeDatabase()
        rows = []
        mixed_invalid_matrix(db, lambda *row: rows.append(row))
        self.assertEqual(tuple(MIXED_INVALID_VALUES), EXPECTED_FIELD_NAMES)
        self.assertEqual(48, len(db.scripts))
        self.assertEqual("PGJT-112/mixed-invalid-values-atomic", rows[0][0])
        self.assertTrue(rows[0][1])
        for script in db.scripts:
            self.assertIn("failure_state<>'PT422'", script)
            self.assertIn("before_state IS DISTINCT FROM after_state", script)
            self.assertIn("IF NOT rejected OR", script)

    def test_unknown_operation_is_a_harness_error(self):
        with self.assertRaises(ValueError):
            mixed_statement("typo", [mixed_document(EXPECTED_FIELD_NAMES, 100000)])
        with self.assertRaises(ValueError):
            mixed_script("insert", EXPECTED_FIELD_NAMES, "typo")


if __name__ == "__main__":
    unittest.main()
