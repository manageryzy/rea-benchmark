"""Offline typed policy/identity controls; no task preparation or analysis."""
import copy
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import action_cache
from capabilities import config, coordinator
from check_evidence_policy import fixture


class CapabilityPagingControls(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden')))
        self.enterContext(patch.object(subprocess, 'Popen', side_effect=AssertionError('Analysis/process forbidden')))
        self.settings, _ = config.load()

    def test_typed_and_smoke_select_the_same_compact_policy(self):
        self.assertEqual(self.settings['condition'], 'lean-evidence-v4')
        policy = config.paging_policy(self.settings)
        self.assertEqual(policy['id'], self.settings['condition'])
        self.assertTrue(policy['compact_semantic_views'])
        smoke = json.loads((action_cache.ROOT / 'benchmarks/config/benchmark-smoke.json').read_bytes())
        self.assertEqual(smoke['condition'], self.settings['condition'])

    def test_historical_v2_remains_loadable_but_inline_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='rea-typed-policy-') as temporary:
            path = Path(temporary) / 'settings.json'
            old = {**self.settings, 'condition': 'paged-evidence-v2'}
            path.write_text(json.dumps(old))
            loaded, _ = config.load(path)
            self.assertNotIn('compact_semantic_views', config.paging_policy(loaded))
            path.write_text(json.dumps({**old, 'condition': 'lossless-evidence-v1'}))
            with self.assertRaisesRegex(ValueError, 'evidence condition'):
                config.load(path)

    def test_condition_changes_namespace_without_mutating_old_settings(self):
        model = {'id': 'offline', 'harness': 'deepseek-harness'}
        old = {**self.settings, 'condition': 'paged-evidence-v2'}
        before = copy.deepcopy(old)
        self.assertNotEqual(coordinator._namespace(old, model), coordinator._namespace(self.settings, model))
        self.assertEqual(old, before)

    def test_typed_action_binds_the_selected_policy_bytes(self):
        keys = []
        for condition in ('paged-evidence-v2', 'compact-evidence-v3', 'lean-evidence-v4'):
            identity, inputs, files = fixture(condition)
            identity['experiment'] = 'capabilities-v1'
            policy = config.paging_policy({'condition': condition})
            expected = ('benchmarks/config/evidence-paging-lean.json' if condition == 'lean-evidence-v4' else 'benchmarks/config/evidence-paging-compact.json' if policy.get('compact_semantic_views')
                        else 'benchmarks/config/evidence-paging.json')
            self.assertEqual(json.loads(files[expected]), policy)
            keys.append(action_cache.inference(identity, inputs, recorded_files=files).key)
        self.assertNotEqual(*keys)


if __name__ == '__main__':
    unittest.main(verbosity=2)
