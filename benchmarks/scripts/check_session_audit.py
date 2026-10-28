"""Focused, offline historical-session decoding controls; no model entrypoint."""
import json
import subprocess
import unittest
from unittest.mock import patch

import audit_condition
import audit_environment as audit
import runtime_image_acceptance as acceptance


class SessionAudit(unittest.TestCase):
    def compress(self, raw):
        return subprocess.run([audit.require_zstd(), '-q', '-c'], input=raw,
                              capture_output=True, check=True).stdout

    def test_real_session_roundtrip_preserves_exact_unicode_and_newlines(self):
        raw = b'{"type":"session","id":"fixture"}\n\n' + json.dumps(
            {'type': 'tool/result', 'text': '定位\n日本語'}, ensure_ascii=False).encode() + b'\n'
        compressed = self.compress(raw)
        self.assertEqual(audit.durable_session(compressed, 'fixture'),
                         [json.loads(line) for line in raw.splitlines() if line.strip()])

    def test_incorrect_identity_is_not_certified(self):
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            audit.durable_session(self.compress(b'{"id":"other"}\n'), 'fixture')

    def test_empty_session_is_not_certified(self):
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            audit.durable_session(self.compress(b''), 'fixture')

    def test_truncated_compressed_session_is_not_accepted(self):
        compressed = self.compress(b'{"id":"fixture"}\n')
        with self.assertRaises(subprocess.CalledProcessError):
            audit.durable_session(compressed[:-2], 'fixture')

    def test_invalid_json_is_not_repaired_or_accepted(self):
        with self.assertRaises(json.JSONDecodeError):
            audit.durable_session(self.compress(b'{"id":"fixture",}\n'), 'fixture')

    def test_dependency_failure_precedes_expensive_record_collection(self):
        for entrypoint in (audit.main, audit_condition.main):
            with self.subTest(entrypoint=entrypoint.__module__), \
                 patch.object(audit.shutil, 'which', return_value=None), \
                 patch.object(audit.records, 'collect') as collect:
                with self.assertRaisesRegex(RuntimeError, 'pinned runtime'):
                    entrypoint()
                collect.assert_not_called()

    def test_actual_image_admission_codec(self):
        acceptance.check_session_compression({'PATH': '/usr/bin:/bin'})

    def test_configured_image_includes_audit_dependency(self):
        policy = json.loads((audit.ROOT/'benchmarks/config/runtime-image.json').read_bytes())
        self.assertIn('zstd', policy['commands'])


if __name__ == '__main__':
    unittest.main()
