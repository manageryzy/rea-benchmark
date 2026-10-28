"""Focused offline controls for report filesystem snapshots; no benchmark calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from report_io import metadata_snapshot, publish_timing
import records
import json


class SnapshotControls(unittest.TestCase):
    def test_command_timing_stays_in_private_report(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);config=root/'benchmarks/config';config.mkdir(parents=True)
            (config/'benchmark-status.json').write_text(json.dumps({'selected_tasks':3}))
            for directory in ('benchmarks/reports','benchmarks/site','docs'):
                path=root/directory;path.mkdir(parents=True,exist_ok=True)
                (path/'index.html').write_text('reader report')
                (path/'diagnostics.html').write_text('<main></main>')
            with patch.object(records,'ROOT',root):publish_timing(1.0)
            self.assertTrue((root/'benchmarks/reports/timings.html').is_file())
            for directory in ('benchmarks/site','docs'):
                self.assertFalse((root/directory/'timings.html').exists())
                self.assertEqual((root/directory/'diagnostics.html').read_text(),'<main></main>')

    def test_cached_directory_queries_remain_bounded_and_freshly_checked(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);original=os.stat;calls=[]
            def counted(path,*args,**kwargs):
                if os.fspath(path)==str(root):calls.append(path)
                return original(path,*args,**kwargs)
            with patch.object(os,'stat',counted):
                with metadata_snapshot():
                    for _ in range(1000):self.assertTrue(root.is_dir())
                    (root/'unrelated-cache-entry').write_text('new')
                    self.assertTrue(root.is_dir())
            self.assertGreaterEqual(len(calls),2,'Missing fresh final directory check')
            self.assertLess(len(calls),10,'Repeated ancestor queries bypass the report cache')

    def test_directory_replacement_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            parent=Path(name)/'input';parent.mkdir()
            with self.assertRaisesRegex(ValueError,'filesystem changed'):
                with metadata_snapshot():
                    self.assertTrue(parent.is_dir())
                    parent.rename(parent.with_name('previous'));parent.mkdir()

    def test_same_size_file_change_with_preserved_mtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'input';p.write_bytes(b'abcd');stamp=p.stat()
            with self.assertRaisesRegex(ValueError,'filesystem changed'):
                with metadata_snapshot():
                    self.assertTrue(p.is_file());p.write_bytes(b'wxyz')
                    os.utime(p,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))

    def test_symlink_retarget_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);a=root/'a';b=root/'b';a.write_text('same');b.write_text('same')
            link=root/'link';link.symlink_to('a')
            with self.assertRaisesRegex(ValueError,'filesystem changed'):
                with metadata_snapshot():
                    self.assertEqual(os.readlink(link),'a');link.unlink();link.symlink_to('b')

if __name__=='__main__':unittest.main()
