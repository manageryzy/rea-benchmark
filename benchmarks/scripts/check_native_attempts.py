"""Focused native action routing/report controls; no provider or compiler."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
import cache
import native_attempts as attempts
import records
import run


class NativeAttempts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trial = 'benchmarks/runs/test/conditions/compact-evidence-v3/model/cpp'
        self.entry = {'directory': self.trial, 'inference_trial': self.trial}

    def write(self, key, legacy=False, terminal=False):
        directory = self.trial if legacy else self.trial + '/actions/' + key
        task = {'directory': directory, 'inference_trial': self.trial, 'fingerprint': key}
        path = self.root / directory
        path.mkdir(parents=True, exist_ok=True)
        (path/'task.json').write_text(json.dumps(task))
        if terminal:
            (path/'budget-exhausted.json').write_text('{}')
        return task

    def test_matching_and_terminal_attempts_are_never_retried(self):
        for legacy in (False, True):
            task = self.write('a'*64, legacy, True)
            self.assertEqual(attempts.route(self.entry, 'a'*64, root=self.root), task['directory'])
            self.assertIn('interrupted', attempts.status(self.root/task['directory']))

    def test_changed_action_preserves_original_bytes(self):
        old = self.write('a'*64, True, True)
        path = self.root/old['directory']/'task.json'
        before = path.read_bytes()
        new = attempts.route(self.entry, 'b'*64, root=self.root)
        self.assertEqual(new, self.trial+'/actions/'+'b'*64)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(attempts.status(self.root/new), 'not_started')

    def test_logical_trial_is_stable_after_physical_routing(self):
        key = 'c'*64
        task = self.write(key)
        self.assertEqual(cache.inference_trial(task), cache.inference_trial(self.entry))
        a = action_cache.Action('inference', {'trial': cache.inference_trial(task).encode()})
        b = action_cache.Action('inference', {'trial': cache.inference_trial(self.entry).encode()})
        self.assertEqual(a.key, b.key)

    def test_current_and_historical_report_rows_are_separate(self):
        old = self.write('a'*64, True)
        new = self.write('b'*64)
        attempts.select(self.entry, new, root=self.root)
        rows = attempts.report_directories(self.root/self.trial, root=self.root)
        self.assertEqual([(p.name,c) for p,c,k in rows], [('b'*64,True),('cpp',False)])
        data = {'conditions': {'active_case_conditions': {'record-parser': 'compact-evidence-v3'}},
                'condition_records': [{'condition':'compact-evidence-v3', 'case':{'suite':'record-parser'},
                    'attempt_current':c,'attempt_id':k} for p,c,k in rows]}
        self.assertEqual([r['attempt_id'] for r in records.active_condition_rows(data)], ['b'*64])

    def test_unidentified_or_corrupt_attempt_is_rejected(self):
        path = self.root/self.trial
        path.mkdir(parents=True)
        (path/'events.jsonl').write_text('')
        with self.assertRaises(ValueError):
            attempts.route(self.entry,'a'*64,root=self.root)
        self.write('a'*64, True)
        new = self.write('b'*64)
        (self.root/new['directory']/'task.json').write_text(json.dumps({**new,'fingerprint':'c'*64}))
        with self.assertRaises(ValueError):
            attempts.route(self.entry,'b'*64,root=self.root)

    def test_pointer_cannot_escape_case(self):
        self.write('a'*64, True)
        pointer = {'schema_version':1,'inference_trial':self.trial,'directory':'elsewhere','fingerprint':'b'*64}
        (self.root/self.trial/'selected-attempt.json').write_text(json.dumps(pointer))
        with self.assertRaises(ValueError):
            attempts.report_directories(self.root/self.trial,root=self.root)

    def test_prepare_next_cell_resolves_before_status_and_dispatch(self):
        self.write('a'*64, True, True)
        task = {'directory':self.trial+'/actions/'+'b'*64,'fingerprint':'b'*64}
        entry = {**self.entry,'suite':'record-parser','status':'unchecked'}
        with patch.object(run,'ROOT',self.root), patch.object(run,'prepare_task',return_value=task):
            result = run.prepare_next_cell(entry, {'id':'model'},None,None)
        self.assertEqual(result['status'],'not_started')
        self.assertEqual(result['resolved_fingerprint'],'b'*64)


if __name__ == '__main__':
    unittest.main()
