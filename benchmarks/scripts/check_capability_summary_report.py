"""Focused report receipt compatibility; isolated AC/CAS, zero model calls."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import action_cache
import validation_cache
from capabilities import summary_report as summary

SOURCE = '''import unittest
class TypedCoordinatorControls(unittest.TestCase):
    def test_example(self): self.assertEqual(2 + 2, 4)
'''


class SummaryReportControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='rea-summary-report-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.enterContext(patch.object(summary, 'ROOT', self.root))
        (self.root/'input.txt').write_bytes(b'frozen input')
        for name in validation_cache.PROJECTED:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(SOURCE if name==validation_cache.PROJECTED[0] else '')
        self.plan=self.make_plan()
        self.cache=action_cache.Cache(self.root/'.cache/benchmark-actions/v3')
        # Populate only this isolated fixture from an actual passing unittest.
        namespace={};exec(SOURCE,namespace)
        result=unittest.TextTestRunner(stream=io.StringIO()).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(namespace['TypedCoordinatorControls']))
        self.assertTrue(result.wasSuccessful())
        self.plan.commit(self.cache,'test_example',{'checks':result.testsRun,
            'passed':result.wasSuccessful(),'provider_launches':0,'model_api_calls':0,
            'tests':[{'name':'test_example','status':'passed'}]})
        self.receipt={'schema_version':2,'kind':validation_cache.VERSION,'passed':True,'checks':1,'failed':0,
            'provider_launches':0,'model_api_calls':0,'actions':{'test_example':self.plan.actions['test_example'].key}}
        self.path=self.root/'receipt.json'
        self.reads=[]
        self.enterContext(patch.object(summary,'coordinator_plan',side_effect=self.make_plan))

    def make_plan(self):
        return validation_cache.Plan(self.root,(),['test_example'],
            inputs={'input.txt':(self.root/'input.txt').read_bytes()})

    def read(self,path):
        self.reads.append(str(Path(path).relative_to(self.root)))
        return Path(path).read_bytes()

    def project(self,value=None,name='coordinator'):
        self.path.write_text(json.dumps(value if value is not None else self.receipt))
        return summary.control_row(name,self.path,lambda:{'input.txt':'unused'},self.read)

    def test_current_schema2_uses_real_action_cache_and_tracks_proofs(self):
        row=self.project()
        self.assertEqual(row,{'id':'coordinator','passed':True,'current':True,'checks':1,'failed':0})
        self.assertIn('input.txt',self.reads)
        self.assertTrue(any('/ac/' in name for name in self.reads))
        self.assertTrue(any('/cas/' in name for name in self.reads))

    def test_stale_action_retains_historical_pass_without_currentness(self):
        (self.root/'input.txt').write_bytes(b'changed input')
        row=self.project()
        self.assertTrue(row['passed']);self.assertFalse(row['current'])

    def test_missing_action_is_not_current(self):
        self.cache._path('ac',self.plan.actions['test_example'].key).unlink()
        row=self.project()
        self.assertTrue(row['passed']);self.assertFalse(row['current'])

    def test_corrupt_action_proof_is_not_current(self):
        path=self.cache._path('ac',self.plan.actions['test_example'].key)
        path.write_bytes(b'{invalid')
        row=self.project()
        self.assertTrue(row['passed']);self.assertFalse(row['current'])

    def test_schema_versions_and_nonoffline_counts_rejected(self):
        for update in ({'schema_version':3},{'model_api_calls':1},{'provider_launches':1},{'model_api_calls':False}):
            with self.subTest(update=update):
                with self.assertRaisesRegex(ValueError,'confirmed offline'):
                    self.project({**self.receipt,**update})
        with self.assertRaisesRegex(ValueError,'confirmed offline'):
            self.project(name='c_buffers')

    def test_malformed_counts_or_cache_receipt_rejected(self):
        for update in ({'checks':True},{'failed':-1},{'checks':0},{'kind':'invented'},{'actions':[]}):
            with self.subTest(update=update):
                with self.assertRaises(ValueError):self.project({**self.receipt,**update})

    def test_legacy_schema1_remains_supported(self):
        value={'schema_version':1,'model_api_calls':0,'passed':True,'checks':1,'failed':0,
            'inputs_sha256':{'input.txt':hashlib.sha256((self.root/'input.txt').read_bytes()).hexdigest()}}
        row=self.project(value,name='localization');self.assertTrue(row['current']);self.assertTrue(row['passed'])
        (self.root/'input.txt').write_bytes(b'legacy changed')
        row=self.project(value,name='localization');self.assertFalse(row['current']);self.assertTrue(row['passed'])

    def test_projection_does_not_rewrite_receipts_or_publish_actions(self):
        self.path.write_text(json.dumps(self.receipt));raw=self.path.read_bytes()
        files={str(p.relative_to(self.root)):p.read_bytes() for p in self.cache.root.rglob('*') if p.is_file()}
        with patch.object(action_cache.Cache,'commit',side_effect=AssertionError('report cannot publish')):
            row=summary.control_row('coordinator',self.path,lambda:{},self.read)
        self.assertTrue(row['current']);self.assertEqual(self.path.read_bytes(),raw)
        self.assertEqual(files,{str(p.relative_to(self.root)):p.read_bytes() for p in self.cache.root.rglob('*') if p.is_file()})


if __name__=='__main__':
    unittest.main(verbosity=2)
