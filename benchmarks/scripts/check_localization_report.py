"""Focused offline controls: representation changes must not repair semantics."""
import copy
import json
import unittest

from localization_report import normalize, effective
from grading import localization


class LocalizationCompatibility(unittest.TestCase):
    def base(self):
        return {'schema_version': 1, 'binary_sha256': 'a' * 64, 'status': 'present',
                'roots': [16], 'sites': [{'role': 'decode', 'start': 16, 'end': 20}],
                'dispatch': [{'condition': 'each_byte', 'site': 18, 'target': 32}],
                'references': [{'site': 18, 'object': 512, 'length': 8, 'access': 'read'}],
                'chain': {'root': 16, 'nodes': [16, 32], 'edges': [[16, 32]]}}

    def run_value(self, v, kind='localization'):
        raw, changes = normalize(json.dumps(v).encode(), kind)
        return json.loads(raw), changes

    def test_integer_document_is_identity(self):
        value = self.base()
        self.assertEqual(self.run_value(value), (value, []))

    def test_exact_hex_and_decimal_positions(self):
        v = self.base()
        v['roots'] = ['0x10']; v['chain']['root'] = '16'
        v['chain']['nodes'] = ['16', '0X20']; v['chain']['edges'] = [['0x10', '32']]
        v['sites'][0].update(start='0x10', end='20')
        v['references'][0].update(site='18', object='0x200', length='8')
        self.assertEqual(self.run_value(v)[0], self.base())

    def test_explicit_rva_wrapper_preserves_metadata_in_receipt(self):
        v = self.base(); v['roots'] = [{'rva': '0x10', 'name': 'decode', 'note': 'submitted'}]
        out, changes = self.run_value(v)
        self.assertEqual(out, self.base())
        self.assertEqual(changes[0]['original'], v['roots'][0])

    def test_range_root_is_not_guessed(self):
        v = self.base(); v['roots'] = [{'start': 16, 'end': 20}]
        with self.assertRaises(ValueError): self.run_value(v)

    def test_conflicting_address_is_not_guessed(self):
        v = self.base(); v['roots'] = [{'rva': 16, 'address': 32}]
        with self.assertRaises(ValueError): self.run_value(v)

    def test_dispatch_range_is_not_callsite(self):
        v = self.base(); v['dispatch'][0]['site'] = {'start': 16, 'end': 20}
        with self.assertRaises(ValueError): self.run_value(v)

    def test_no_va_to_rva_rebasing(self):
        v = self.base(); v['roots'] = ['0x401010']
        self.assertEqual(self.run_value(v)[0]['roots'], [0x401010])

    def test_reject_nonexact_numbers(self):
        for item in [True, 16.0, '1.6e1', '016', '-1', '+16', ' 16', '0x', str(2**64)]:
            with self.subTest(item=item):
                v = self.base(); v['roots'] = [item]
                with self.assertRaises(ValueError): self.run_value(v)

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(ValueError):
            normalize(b'{"schema_version":1,"schema_version":1}', 'localization')

    def test_original_limits_and_unknown_fields_remain(self):
        for mutate in [lambda v: v['sites'][0].update(end=200), lambda v: v.update(extra='x'),
                       lambda v: v['chain'].update(edges=[[16, 99]])]:
            v = self.base(); mutate(v)
            with self.assertRaises(ValueError): self.run_value(v)

    def test_optimized_exact_ranges_and_bounds(self):
        v = {'schema_version': 1, 'binary_sha256': 'a' * 64,
             'instances': [{'ranges': [{'start': '0x10', 'end': '0x20'}]}]}
        out, changes = self.run_value(v, 'optimized_localization')
        self.assertEqual(out['instances'][0]['ranges'], [{'start': 16, 'end': 32}])
        v['instances'][0]['ranges'][0]['end'] = '9999'
        with self.assertRaises(ValueError): self.run_value(v, 'optimized_localization')

    def test_idempotence(self):
        v = self.base(); v['roots'] = ['0x10']
        raw, _ = normalize(json.dumps(v).encode(), 'localization')
        self.assertEqual(normalize(raw, 'localization'), (raw, []))

    def test_bad_format_is_unmeasured_but_wrong_semantics_stays_zero(self):
        row = {'capability_ids': ['L1'], 'status': 'graded', 'grade': {'artifact_valid': False, 'passed': 0}}
        self.assertIsNone(effective(row, {})['grade'])
        row['grade']['artifact_valid'] = True
        self.assertEqual(effective(row, {})['grade']['passed'], 0)
        self.assertEqual(row['status'], 'graded')

    def test_nonlocalization_unchanged(self):
        row = {'capability_ids': ['C1'], 'status': 'graded', 'grade': {'artifact_valid': False, 'passed': 0}}
        self.assertIs(effective(row, {}), row)

    def test_overlay_is_bound_to_original_grade_and_target(self):
        from check_report_aggregate import typed
        row = typed(); row['capability_ids'] = ['L1']; row['grade'].update(grading_id='old', artifact_valid=False)
        entry = {'observation': copy.deepcopy(row), 'original_grading_id': 'old',
                 'grade': {'grading_id': 'new', 'artifact_valid': True, 'passed': 1}, 'revision_id': 'new'}
        changed = effective(row, {'saved': entry})
        self.assertEqual(changed['grade']['passed'], 1)
        self.assertFalse(row['grade']['artifact_valid'])
        for key in ['binary_sha256', 'public_package_sha256', 'task_id']:
            bad = copy.deepcopy(entry); bad['observation'][key] = 'different'
            self.assertIsNone(effective(row, {'saved': bad})['grade'])
        bad = copy.deepcopy(entry); bad['original_grading_id'] = 'different'
        self.assertIsNone(effective(row, {'saved': bad})['grade'])

    def test_format_review_excluded_from_radar_and_valid_zero_retained(self):
        import report_views
        from check_report_aggregate import typed, data
        for valid in [False, True]:
            row = typed(passed=0); row['capability_ids'] = ['L1']; row['grade']['artifact_valid'] = valid
            value = data(); value['capability_measurements'] = {'cells': [{**row, 'observation': row}], 'history': [row]}
            before = copy.deepcopy(value)
            cells = report_views.aggregate_cells(value)
            dimensions = report_views.aggregate_dimensions(cells, value['models'])
            score = dimensions[0]['models'][0]['values'][0]
            self.assertEqual(score['n'], int(valid))
            self.assertEqual(score['score'], 0 if valid else None)
            self.assertEqual(value, before)

    def test_staged_prompts_change_only_serialization_contract(self):
        import ast
        from pathlib import Path
        from localization_report import ROOT, STORE, sha
        manifest_path = STORE / 'prompt-patch-manifest.json'
        if not manifest_path.exists():
            self.skipTest('One-time staged prompt fixture is not present in this checkout')
        manifest = json.loads(manifest_path.read_bytes())
        for p in manifest['patches']:
            old = (ROOT / p['target']).read_bytes(); new = (STORE / p['candidate']).read_bytes()
            if sha(old) == p['candidate_sha256']:
                old = (STORE / ('original-' + p['original_sha256'] + '.py')).read_bytes()
            self.assertEqual(sha(old), p['original_sha256']); self.assertEqual(sha(new), p['candidate_sha256'])
            before, after = ast.parse(old), ast.parse(new)
            remove = lambda tree: ast.dump(ast.Module(body=[n for n in tree.body if not
                isinstance(n, ast.FunctionDef) or n.name != 'prompt'], type_ignores=[]))
            self.assertEqual(remove(before), remove(after))
            # Execute only the prompt definition, with the production module's
            # existing read-only helpers. No preparation or model call occurs.
            import importlib
            module = importlib.import_module('capabilities.locations' if p['target'].endswith('/locations.py')
                                             else 'capabilities.optimized_locations.scoring')
            namespace = dict(vars(module))
            node = next(n for n in after.body if isinstance(n, ast.FunctionDef) and n.name == 'prompt')
            exec(compile(ast.Module(body=[node], type_ignores=[]), p['candidate'], 'exec'), namespace)
            text = namespace['prompt']('record_codec', 'L1') if p['target'].endswith('/locations.py') else namespace['prompt']()
            self.assertIn('JSON integer NUMBERS', text); self.assertIn('4660', text)
            self.assertIn('no unknown or duplicate keys', text.lower())


if __name__ == '__main__':
    unittest.main()
