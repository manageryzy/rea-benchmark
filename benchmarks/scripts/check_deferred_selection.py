"""Focused declaration/admission boundary tests. No model, build or full suite."""
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import run
from capabilities import coordinator as c


class SelectionChecks(unittest.TestCase):
    def setUp(self):
        self.guard = ExitStack(); self.addCleanup(self.guard.close)
        self.settings = {'inference_enabled': True, 'condition': 'test', 'limits': {}, 'runtime': {}}
        self.models = {name: {'id': name} for name in ('a', 'b')}
        self.declarations = [{'task_id': name, 'profile': {'enabled': True, 'kind': 'pointer_probe'}} for name in ('one', 'two')]
        self.selection = {'models': ['a', 'b'], 'suites': ['capabilities-v1'], 'experiments': ['capabilities-v1'],
                          'cases': 'all', 'condition': 'test', 'capabilities': True}
        for owner, name, kwargs in (
            (c.config, 'load', {'return_value': (self.settings, [])}),
            (c.tasks, 'declared', {'return_value': self.declarations}),
            (c.records, 'models', {'return_value': self.models}),
            (c.records, 'registry', {'return_value': {'experiments': []}}),
            (c.evidence_access, 'validate_selection', {'return_value': 'binary'}),
            (c, '_namespace', {'side_effect': lambda settings, model, access='binary': settings['condition'] + model['id']}),
        ): self.guard.enter_context(patch.object(owner, name, **kwargs))
        self.guard.enter_context(patch.dict(os.environ, {'REA_BENCHMARK_OFFLINE': '0'}))

    def test_native_cached_cell_is_inspected_only_when_executed_and_uses_cache_admission(self):
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); directory = root / 'attempt'; directory.mkdir()
            (directory / 'result.json').write_text('{}')
            (directory / 'grade.json').write_text('{}')
            entry = {'experiment': 'native', 'suite': 'native', 'case': 'cpp', 'model': 'a',
                     'condition': 'test', 'status': 'unchecked', 'directory': 'attempt'}
            with (patch.object(run, 'ROOT', root), patch.object(run.records, 'ROOT', root),
                  patch.object(run, '_execution_allowed'),
                  patch.object(run, 'prepare_task', return_value={'fingerprint': 'expected'}) as prepare,
                  patch.object(run.cache, 'validate', return_value={'status': 'cache_hit'}) as validate,
                  patch.object(run, 'account_saved', return_value='saved'),
                  patch.object(run, 'budget_outcome', return_value={}),
                  patch.object(run, 'launch_model', side_effect=AssertionError('provider'))):
                result = run.execute(entry, {'id': 'a'})
            self.assertEqual(result['provider_launches'], 0)
            self.assertEqual(validate.call_args.args[0]['status'], 'cached')
            prepare.assert_called_once()
            self.assertEqual(entry['status'], 'unchecked')

    def test_selection_does_not_touch_frozen_spec_cache_or_provider(self):
        with (patch.object(c.preparation, 'inspect', side_effect=AssertionError('selection inspected')),
              patch.object(c.tasks, 'frozen', side_effect=AssertionError('selection froze')),
              patch.object(c.store, 'spec', side_effect=AssertionError('selection loaded spec')),
              patch.object(c.runtime_attestation, 'capture', side_effect=AssertionError('runtime'))):
            rows = run.selected_entries(self.selection)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['status'] for r in rows}, {'preparation_unchecked'})
        self.assertTrue(all(r['directory'] is None for r in rows))
        self.assertTrue(run.needs_installed_runtime(rows, False))

    def test_inspection_is_once_per_case_not_per_model_and_no_repeat_for_resolved_rows(self):
        rows = run.selected_entries(self.selection)
        with patch.object(c.preparation, 'inspect', return_value={'status': 'pending'}) as inspect:
            resolved = c.inspect_selected(rows)
            self.assertEqual(inspect.call_count, 2)
            self.assertEqual(c.inspect_selected(resolved), resolved)
            self.assertEqual(inspect.call_count, 2)
        self.assertEqual({r['status'] for r in resolved}, {'preparation_pending'})

    def test_changed_configuration_cannot_inherit_selection(self):
        rows = run.selected_entries(self.selection)
        with (patch.object(c, '_namespace', return_value='changed'),
              patch.object(c.preparation, 'inspect', return_value={'status': 'pending'})):
            with self.assertRaisesRegex(RuntimeError, 'configuration changed'):
                c.inspect_selected(rows)

    def test_removed_declaration_is_not_silently_dropped(self):
        rows = run.selected_entries(self.selection)
        with (patch.object(c.tasks, 'declared', return_value=self.declarations[:1]),
              patch.object(c.preparation, 'inspect', return_value={'status': 'pending'})):
            with self.assertRaises(RuntimeError): c.inspect_selected(rows)

    def test_cache_corruption_is_not_relabelled_as_hit(self):
        rows = run.selected_entries(self.selection)
        with patch.object(c.preparation, 'inspect', return_value={'status': 'invalid', 'reason': 'corrupt CAS'}):
            resolved = c.inspect_selected(rows)
        self.assertEqual({r['status'] for r in resolved}, {'preparation_invalid'})
        self.assertTrue(all(r['directory'] is None for r in resolved))

    def test_ready_cache_metadata_and_action_namespace_are_preserved(self):
        rows = run.selected_entries(self.selection)
        def state(row):
            return {'status': 'ready', 'case': {'id': row['task_id'], 'public_package_sha256': 'a' * 64}}
        with (patch.object(c.preparation, 'inspect', side_effect=state) as inspect,
              patch.object(c.attempts, 'status', return_value='cached')):
            resolved = c.inspect_selected(rows)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual({r['status'] for r in resolved}, {'cached'})
        self.assertTrue(all(r['directory'].endswith('/' + r['configuration_sha256']) for r in resolved))


if __name__ == '__main__':
    unittest.main()
