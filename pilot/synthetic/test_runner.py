#!/usr/bin/env python3
"""Unit checks for the synthetic rehearsal without a database or PostgREST."""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import sys

import run_pilot as pilot
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.verify_test_coverage import MANIFEST


class RunnerTests(unittest.TestCase):
    def make_runner(self, directory):
        args = pilot.arguments(['--output', str(Path(directory) / 'run'),
                                '--database', 'unit_pilot', '--restore-database', 'unit_restore'])
        return pilot.Rehearsal(args)

    def test_current_fixture_has_five_roots_and_ten_children(self):
        documents = [json.loads(path.read_text()) for path in sorted((pilot.PILOT / 'documents').glob('*.json'))]
        self.assertEqual((5, 10), pilot.fixture_cardinality(documents))
        changed = copy.deepcopy(documents)
        changed.append({'_id': 999, 'items': [{'itemId': 999}]})
        self.assertEqual((6, 11), pilot.fixture_cardinality(changed))
        for invalid in ([], [documents[0], documents[0]]):
            with self.assertRaises(ValueError):
                pilot.fixture_cardinality(invalid)

    def test_mixed_fixture_places_non_character_types_before_text(self):
        spec = json.loads((pilot.PILOT / 'mixed_types/spec.json').read_text())
        names = sorted(spec['root']['fields'], key=lambda name: (len(name.encode()), name.encode()))
        self.assertEqual(['n', 'ok', 'observed', 'description'], names)
        docs = json.loads((pilot.PILOT / 'mixed_types/documents.json').read_text())
        self.assertEqual(2, len(docs))
        self.assertIsInstance(docs[0]['n'], int)
        self.assertIsInstance(docs[0]['ok'], bool)
        self.assertIn('T', docs[0]['observed'])

    def test_normalisation_preserves_inputs_and_timezone_semantics(self):
        original = {'_id': 1, '_metadata': {'etag': 'token'}, 'observed': '2026-11-10T10:15:00Z'}
        before = copy.deepcopy(original)
        self.assertEqual({'_id': 1, 'observed': '2026-11-10T10:15:00+00:00'}, pilot.normalise(original))
        self.assertEqual(before, original)
        self.assertIsNone(pilot.normalise(None))

    def test_database_names_cannot_identify_broad_or_option_targets(self):
        for name in ('postgres', 'template0', 'template1', '--force', '', 'x;DROP DATABASE y', 'x' * 64):
            with self.subTest(name=name), self.assertRaises(ValueError):
                pilot.validate_databases(name, 'restore_pilot')
        with self.assertRaises(ValueError):
            pilot.validate_databases('same', 'same')

    def test_existing_databases_are_refused_without_any_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.scalar = Mock(return_value='1')
            runner.run = Mock()
            with self.assertRaisesRegex(RuntimeError, 'refusing existing'):
                runner.setup()
            self.assertEqual([], runner.databases)
            runner.run.assert_not_called()
            self.assertEqual([], runner.cleanup())
            runner.run.assert_not_called()

    def test_createdb_is_tracked_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.run = Mock(side_effect=RuntimeError('createdb failed'))
            with self.assertRaises(RuntimeError):
                runner.create_database('unit_pilot')
            self.assertEqual([], runner.databases)
            runner.run = Mock()
            runner.create_database('unit_pilot')
            self.assertEqual(['unit_pilot'], runner.databases)

    def test_cleanup_only_removes_created_resources_and_continues_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.databases = ['unit_pilot', 'unit_restore']
            runner.roles = ['owned_app', 'owned_auth']
            runner.run = Mock(side_effect=[RuntimeError('drop failed'), None])
            runner.sql = Mock()
            errors = runner.cleanup()
            self.assertEqual(1, len(errors))
            self.assertEqual(['unit_restore', 'unit_pilot'],
                             [call.args[0][-1] for call in runner.run.call_args_list])
            self.assertEqual(['DROP ROLE owned_auth;', 'DROP ROLE owned_app;'],
                             [call.args[0] for call in runner.sql.call_args_list])

    def test_setup_adds_index_capabilities_health_and_both_views(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.run = Mock()
            runner.sql = Mock()
            runner.scalar = Mock(side_effect=['0', 'OK', 'OK', '5:10'])
            runner.setup()
            queries = '\n'.join(call.args[0] for call in runner.sql.call_args_list)
            self.assertIn('CREATE INDEX ON pilot.order_items(order_id,item_id)', queries)
            self.assertIn('grant_array_reader', queries)
            self.assertEqual(2, queries.count('grant_delete_rpc'))
            self.assertIn("create_duality_view('pilot_api.mixed_dv'", queries)
            self.assertEqual('PASS', runner.checks['PGJT-112/synthetic-health']['status'])
            self.assertEqual(2, len(runner.roles))

    def test_mixed_execution_uses_both_transaction_orders_and_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.sql = Mock()
            runner.scalar = Mock(side_effect=['0', '0', 't', 't'])

            def read(ident, route='/orders_dv'):
                doc = copy.deepcopy(next(doc for doc in runner.mixed_documents if doc['_id'] == ident))
                doc['_metadata'] = {'etag': 'token'}
                return doc

            runner.read = Mock(side_effect=[
                read(1), {**read(1), 'n': 8, 'ok': False, 'description': read(1)['description'] + ' updated'},
                read(2), {**read(2), 'n': 1, 'ok': True, 'description': read(2)['description'] + ' updated'}])
            runner.http = Mock(side_effect=[
                (201, [{'data': runner.mixed_documents[0]}], 1), (200, [], 1),
                (201, [{'data': runner.mixed_documents[1]}], 1), (200, [], 1)])
            runner.mixed_types()
            scripts = [call.args[0] for call in runner.sql.call_args_list]
            self.assertEqual(2, len(scripts))
            self.assertNotEqual(scripts[0], scripts[1])
            self.assertTrue(all('SET LOCAL ROLE' in script and 'ROLLBACK;' in script for script in scripts))
            self.assertTrue(all(call.kwargs['route'] == '/mixed_dv' for call in runner.http.call_args_list))
            self.assertEqual('PASS', runner.checks['PGJT-112/synthetic-mixed-types']['status'])

    def test_early_failure_emits_full_fail_closed_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.setup = Mock(side_effect=RuntimeError('synthetic failure'))
            runner.cleanup = Mock(return_value=[])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, runner.execute())
            rows = [json.loads(line) for line in runner.result_path.read_text().splitlines()]
            self.assertEqual(list(pilot.RESULT_IDS), [row['name'] for row in rows])
            self.assertEqual(14, len(rows))
            self.assertEqual(13, sum(row['status'] == 'FAIL' for row in rows))
            self.assertEqual('NO-GO', json.loads((runner.out / 'summary.json').read_text())['decision'])
            runner.cleanup.assert_called_once()

    def test_coverage_selectors_match_emitted_result_schema(self):
        manifest = json.loads(MANIFEST.read_text())
        synthetic = [row for row in manifest['runtime_evidence']
                     if row['suite'] == 'synthetic_pilot']
        self.assertTrue(synthetic)
        emitted = [{'name': name, 'status': 'PASS'} for name in pilot.RESULT_IDS]
        for selector in synthetic:
            self.assertEqual('name', selector['field'])
            self.assertEqual(1, sum(row.get(selector['field']) == selector['value']
                                    for row in emitted))

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            with self.assertRaises(FileExistsError):
                pilot.Rehearsal(runner.args)
            runner.result_path.write_text('existing evidence')
            with self.assertRaisesRegex(ValueError, 'never overwrite'):
                pilot.Rehearsal(runner.args)
            self.assertEqual('existing evidence', runner.result_path.read_text())

    def test_http_start_uses_private_socket_and_boundary_not_raw_tcp(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            process = Mock()
            process.poll.return_value = None
            with patch.object(pilot.subprocess, 'Popen', return_value=process), \
                    patch.object(pilot.urllib.request, 'urlopen'), \
                    patch.object(pilot, 'Boundary') as boundary, \
                    patch.object(pilot.threading, 'Thread'):
                runner.start_http()
                config = (runner.raw / 'postgrest.conf').read_text()
                self.assertIn('server-unix-socket = ', config)
                self.assertIn('server-unix-socket-mode = "600"', config)
                self.assertIn(f'db-max-rows = {pilot.MAX_QUERY_LIMIT}', config)
                self.assertEqual(['orders_dv', 'mixed_dv'], boundary.call_args.args[2])
                self.assertEqual((runner.args.rest_host, runner.args.rest_port), boundary.call_args.args[0])
                self.assertEqual(0o700, Path(runner.socket_directory.name).stat().st_mode & 0o777)
                self.assertEqual(0o600, (runner.raw / 'postgrest.conf').stat().st_mode & 0o777)
                self.assertEqual([], runner.cleanup())

    def test_success_requires_every_fixed_check_and_successful_cleanup(self):
        for cleanup_errors, expected in (([], 0), (['cleanup failed'], 1)):
            with self.subTest(cleanup_errors=cleanup_errors), tempfile.TemporaryDirectory() as directory:
                runner = self.make_runner(directory)

                def setup():
                    for name in pilot.RESULT_IDS:
                        if name != 'PGJT-112/synthetic-cleanup':
                            runner.add(name, True, 'passed', 'passed')

                runner.setup = setup
                for method in ('start_http', 'orders', 'mixed_types', 'boundary_contract', 'restore'):
                    setattr(runner, method, Mock())
                runner.cleanup = Mock(return_value=cleanup_errors)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(expected, runner.execute())
                rows = [json.loads(line) for line in runner.result_path.read_text().splitlines()]
                self.assertEqual(list(pilot.RESULT_IDS), [row['name'] for row in rows])


if __name__ == '__main__':
    unittest.main()
