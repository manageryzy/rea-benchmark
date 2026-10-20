#!/usr/bin/python3
"""Offline privacy and reproducibility acceptance for the Pages artifact."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import public_report as public
import records


def hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


class PublicAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = public.validate(json.loads(public.SNAPSHOT.read_text()))

    def test_projection_excludes_private_payloads(self):
        row = copy.deepcopy(next(r for r in self.data['records'] if r['grade'] and r['readability']))
        canary = 'PRIVATE_REASONING_AND_CREDENTIAL_CANARY'
        row.update(answer=canary, reasoning=canary, directory='/home/private', raw_json=canary)
        row['source'] = '/home/private/reconstructed.cpp'
        row['receipt'].update(requests=[{'Authorization': canary}], raw_usage=canary, api_key=canary)
        row['grade'].update(failure=canary, first_mismatches=[canary])
        row['readability']['notes'] = canary
        sanitized = public.project_row(row)
        self.assertNotIn(canary, json.dumps(sanitized))
        self.assertNotIn('/home/', json.dumps(sanitized))
        public.validate_value(sanitized, public.ROW_SCHEMA)
        # Sanitization preserves the numeric usage, scores and cost.
        self.assertEqual(sanitized['cost'], row['cost'])
        self.assertEqual(sanitized['receipt']['reported_usage'], row['receipt']['reported_usage'])
        self.assertEqual(sanitized['grade']['passed'], row['grade']['passed'])

    def test_schema_rejects_unknown_fields_and_credentials(self):
        for location in ['root', 'receipt', 'label']:
            data = copy.deepcopy(self.data)
            if location == 'root':
                data['raw_requests'] = []
            elif location == 'receipt':
                next(r for r in data['records'] if r['receipt'])['receipt']['headers'] = {}
            else:
                next(iter(data['models'].values()))['label'] = 'sk-' + 'A' * 32
            with self.assertRaises(ValueError):
                public.validate(data)

    def test_case_links_and_global_metric_consistency(self):
        rows = self.data['records'] + self.data['condition_records']
        self.assertEqual(len(rows), len({(r['experiment'], r['case']['id'], r['model'], r['condition']) for r in rows}))
        active = records.active_condition_rows(self.data)
        self.assertTrue(active)
        self.assertEqual(set(r['model'] for r in active), set(self.data['models']))
        for row in rows:
            if row['grade']:
                self.assertLessEqual(row['grade']['passed'], row['grade']['total'])
                self.assertTrue(row['receipt'])

    def test_reproducible_html_only_without_raw_files_or_model_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'site'
            # Public rendering must not even ask the private record reader for data.
            with patch.object(records, 'read_bytes', side_effect=AssertionError('Private cache read')), \
                 patch.object(records, 'collect', side_effect=AssertionError('Private cache collection')):
                public.build(out)
                first = hashes(out)
                public.build(out)
                self.assertEqual(first, hashes(out))
            receipt = public.validate_site(out)
            self.assertGreater(receipt['pages'], 8)
            self.assertEqual(receipt['model_calls'], 0)
            self.assertFalse(any(Path(p).suffix in ('.json', '.jsonl', '.csv', '.zip', '.md', '.sse') for p in first))
            self.assertTrue((out / 'history/index.html').is_file())
            self.assertTrue((out / 'diagnostics.html').is_file())
            self.assertIn('data-chart-panel="matched"', (out / 'index.html').read_text())
            pages = Path(tmp) / 'docs'
            public.stage_pages(out, pages)
            self.assertEqual(first, hashes(pages))
            public.stage_pages(out, pages)
            self.assertEqual(first, hashes(pages))
            manual = pages / 'manual.md'
            manual.write_text('Authored document: must not be overwritten')
            with self.assertRaises(ValueError):
                public.stage_pages(out, pages)
            self.assertTrue(manual.is_file())
            for name in ['raw.json', 'raw.jsonl', 'trace.sse', 'data.zip']:
                path = out / name
                path.write_text('private canary')
                with self.assertRaises(ValueError):
                    public.validate_site(out)
                path.unlink()
            (out / 'leak.html').symlink_to(out / 'index.html')
            with self.assertRaises(ValueError):
                public.validate_site(out)

    def test_network_and_process_guards_fail_closed(self):
        def attempt_render(report):
            for operation in [lambda: socket.create_connection(('api.example.invalid', 443)),
                              lambda: subprocess.Popen(['model-provider'])]:
                with self.assertRaises(RuntimeError):
                    operation()
            raise RuntimeError('intentional test interruption')
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(public.PublicReport, 'build', attempt_render):
                with self.assertRaisesRegex(RuntimeError, 'intentional test interruption'):
                    public.build(Path(tmp) / 'site')
            self.assertFalse((Path(tmp) / 'site').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
