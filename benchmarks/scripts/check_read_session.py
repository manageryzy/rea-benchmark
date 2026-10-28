"""Mutation controls for read-only admission reuse; no model/tool processes."""
import os
from pathlib import Path
import tempfile
import unittest
from contextvars import ContextVar
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from read_session import ReadSession


class SessionChecks(unittest.TestCase):
    def test_inspection_deduplicates_stats_and_checks_exit_fresh(self):
        import read_session
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'data'; path.write_bytes(b'good')
            observed = []
            original = read_session.signature
            def signature(p):
                if str(p) == str(path): observed.append(1)
                return original(p)
            @session.memoize
            def child(): return path.read_bytes()
            def inspect(): return [child(), child(), path.read_bytes(), path.read_bytes()]
            inspect = session.memoize(inspect, transactional=True)
            with patch.object(read_session, 'signature', signature):
                self.assertEqual(inspect(), [b'good'] * 4)
            self.assertEqual(len(observed), 2)  # First observation + fresh boundary.
            self.assertIsNone(session.epoch)

    def test_inspection_detects_external_same_size_write_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'data'; path.write_bytes(b'good'); stamp = path.stat()
            @session.memoize
            def child(): return path.read_bytes()
            def inspect():
                value = child()
                fd = os.open(path, os.O_WRONLY)
                try: os.write(fd, b'evil')
                finally: os.close(fd)
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                return value
            inspect = session.memoize(inspect, transactional=True)
            with self.assertRaisesRegex(RuntimeError, 'inputs changed'): inspect()
            self.assertFalse(session.pending)
            self.assertIsNone(session.epoch)
            self.assertEqual(child(), b'evil')

    def test_inspection_forbids_writes_and_recovers_after_exception(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'data'; path.write_text('good')
            writer = session.memoize(lambda: path.write_text('evil'), transactional=True)
            with self.assertRaisesRegex(RuntimeError, 'Writing inside'): writer()
            self.assertEqual(path.read_text(), 'good')
            self.assertIsNone(session.epoch)
            self.assertFalse(session.pending)
            reader = session.memoize(path.read_text, transactional=True)
            self.assertEqual(reader(), 'good')

    def test_inspection_boundary_rejects_symlink_and_directory_query_changes(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            root = Path(tmp); a = root/'a'; b = root/'b'; link = root/'link'
            a.write_text('good'); b.write_text('evil'); link.symlink_to(a)
            def retarget():
                value = link.read_bytes(); os.unlink(link); os.symlink(b, link); return value
            with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
                session.memoize(retarget, transactional=True)()
            def add_file():
                value = list(root.glob('*.new'))
                fd = os.open(root/'added.new', os.O_CREAT | os.O_WRONLY, 0o600); os.close(fd)
                return value
            with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
                session.memoize(add_file, transactional=True)()

    def test_inspection_reuses_verified_cas_and_keeps_child_dependencies(self):
        import action_cache
        with tempfile.TemporaryDirectory() as tmp:
            local = action_cache.Cache(tmp); value = local.put(b'good')
            original_get = action_cache.Cache.get
            calls = []
            def get(cache, digest): calls.append(1); return original_get(cache, digest)
            with patch.object(action_cache.Cache, 'get', get), ReadSession({}) as session:
                @session.memoize
                def child(): return local.get(value)
                def inspect(): return local.get(value), child(), local.get(value)
                inspect = session.memoize(inspect, transactional=True)
                self.assertEqual(inspect(), (b'good',) * 3)
                self.assertEqual(len(calls), 1)
                path = Path(tmp)/'cas'/value['hash']; stamp = path.stat(); path.write_bytes(b'evil')
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                with self.assertRaisesRegex(ValueError, 'Corrupt CAS'): child()
                with self.assertRaisesRegex(ValueError, 'Corrupt CAS'): inspect()

    def test_inspection_new_epoch_observes_changed_input(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp)/'data'; path.write_bytes(b'old')
            @session.memoize
            def child(): return path.read_bytes()
            first = session.memoize(lambda: child(), transactional=True)
            second = session.memoize(lambda: child(), transactional=True)
            self.assertEqual(first(), b'old'); path.write_bytes(b'new')
            self.assertEqual(second(), b'new'); self.assertEqual(first(), b'new')

    def test_inspection_cas_mutation_before_exit_cannot_publish_a_hit(self):
        import action_cache
        with tempfile.TemporaryDirectory() as tmp:
            local = action_cache.Cache(tmp); digest = local.put(b'good')
            path = Path(tmp)/'cas'/digest['hash']
            with ReadSession({}) as session:
                def inspect():
                    value = local.get(digest)
                    fd = os.open(path, os.O_WRONLY)
                    try: os.write(fd, b'evil')
                    finally: os.close(fd)
                    return value, local.get(digest)
                with self.assertRaisesRegex(RuntimeError, 'inputs changed'):
                    session.memoize(inspect, transactional=True)()
                self.assertFalse(session.pending)
                self.assertIsNone(session.epoch)

    def test_inspection_cas_reuse_is_scoped_to_storage_root(self):
        import action_cache
        with tempfile.TemporaryDirectory() as tmp:
            a, b = action_cache.Cache(Path(tmp)/'a'), action_cache.Cache(Path(tmp)/'b')
            digest = a.put(b'good'); b.put(b'good')
            (Path(tmp)/'b'/'cas'/digest['hash']).write_bytes(b'evil')
            with ReadSession({}) as session:
                def inspect(): return a.get(digest), b.get(digest)
                with self.assertRaisesRegex(ValueError, 'Corrupt CAS'):
                    session.memoize(inspect, transactional=True)()

    def test_reuse_and_same_size_write_with_restored_mtime(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            p = Path(tmp) / 'data'; p.write_bytes(b'good')
            calls = []
            @session.memoize
            def validate():
                calls.append(1)
                if p.read_bytes() != b'good':
                    raise ValueError('bad bytes')
                return {'ok': True}
            validate()['ok'] = False
            self.assertEqual(validate(), {'ok': True})
            self.assertEqual(len(calls), 1)
            stat = p.stat(); p.write_bytes(b'evil')
            os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaises(ValueError): validate()
            with self.assertRaises(ValueError): validate()
            self.assertEqual(len(calls), 3)

    def test_added_glob_member_and_missing_file_creation_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            root = Path(tmp); missing = root / 'missing'
            calls = []
            @session.memoize
            def inventory():
                calls.append(1)
                return sorted(p.name for p in root.glob('*.txt')), missing.exists()
            self.assertEqual(inventory(), ([], False)); inventory()
            (root / 'new.txt').write_text('new')
            self.assertEqual(inventory(), (['new.txt'], False))
            missing.touch()
            self.assertEqual(inventory(), (['new.txt'], True))
            self.assertEqual(len(calls), 3)

    def test_symlink_target_and_parent_replacement_invalidate(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            root = Path(tmp); target = root / 'target'; target.write_text('a')
            link = root / 'link'; link.symlink_to(target)
            @session.memoize
            def read(): return link.read_text()
            self.assertEqual(read(), 'a'); target.write_text('b')
            self.assertEqual(read(), 'b')
            directory = root / 'dir'; directory.mkdir(); (directory / 'f').write_text('x')
            @session.memoize
            def read_directory(): return (directory / 'f').read_text()
            self.assertEqual(read_directory(), 'x')
            directory.rename(root / 'old'); directory.symlink_to(root / 'old', target_is_directory=True)
            self.assertEqual(read_directory(), 'x')
            self.assertGreaterEqual(session.invalidations, 2)

    def test_worker_reads_and_mutation_during_call_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            p = Path(tmp) / 'data'; p.write_bytes(b'a')
            @session.memoize
            def worker_read():
                with ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(p.read_bytes).result()
            self.assertEqual(worker_read(), b'a'); self.assertEqual(worker_read(), b'a')
            p.write_bytes(b'b'); self.assertEqual(worker_read(), b'b')
            @session.memoize
            def writes():
                result = p.read_bytes(); p.write_bytes(result + b'x'); return result
            self.assertEqual(writes(), b'b'); self.assertEqual(writes(), b'bx')

    def test_unrelated_content_does_not_invalidate_and_exit_restores(self):
        original = Path.open
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'data'; q = Path(tmp) / 'other'
            p.write_text('a'); q.write_text('old')
            with ReadSession({}) as session:
                @session.memoize
                def read(): return p.read_text()
                read(); q.write_text('new'); self.assertEqual(read(), 'a')
                self.assertEqual(session.hits, 1)
            self.assertIs(Path.open, original)

    def test_controls_context_cannot_be_reused_as_real_admission(self):
        controls = ContextVar('controls', default=False)
        with ReadSession({}) as session:
            @session.memoize
            def gate():
                if controls.get(): return
                raise ValueError('real gate rejected')
            token = controls.set(True)
            gate(); gate(); controls.reset(token)
            with self.assertRaises(ValueError): gate()

    def test_loaded_helper_change_invalidates(self):
        import action_cache
        with ReadSession({}) as session:
            @session.memoize
            def gate():
                return action_cache.canonical({'x': 1})
            gate(); gate()
            with patch.object(action_cache, 'canonical', side_effect=ValueError('changed helper')):
                with self.assertRaises(ValueError): gate()

    def test_nested_hit_transfers_dependencies_and_reuses_direct_admission(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            p = Path(tmp) / 'data'; p.write_text('first')
            calls = []
            @session.memoize
            def child():
                calls.append('child'); return p.read_text()
            @session.memoize
            def parent():
                calls.append('parent'); return child() + child()
            self.assertEqual(child(), 'first')
            self.assertEqual(parent(), 'firstfirst')
            self.assertEqual(parent(), 'firstfirst')
            self.assertEqual(calls, ['child', 'parent'])
            p.write_text('other')
            self.assertEqual(parent(), 'otherother')
            self.assertEqual(calls, ['child', 'parent', 'parent', 'child'])

    def test_nested_miss_publishes_child_and_writes_poison_parent(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            p = Path(tmp) / 'data'; p.write_text('a')
            calls = []
            @session.memoize
            def child():
                calls.append(1); return p.read_text()
            @session.memoize
            def parent(): return child()
            self.assertEqual(parent(), 'a'); self.assertEqual(child(), 'a')
            self.assertEqual(len(calls), 1)
            @session.memoize
            def writer():
                p.write_text(p.read_text() + 'x'); return child()
            @session.memoize
            def writing_parent(): return writer()
            self.assertEqual(writing_parent(), 'ax')
            self.assertEqual(writing_parent(), 'axx')

    def test_nested_exception_does_not_lose_outer_read_set(self):
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            p = Path(tmp) / 'data'; p.write_text('bad')
            @session.memoize
            def child():
                value = p.read_text()
                if value == 'bad': raise ValueError('invalid')
                return value
            @session.memoize
            def parent():
                try: return child()
                except ValueError: return 'rejected'
            self.assertEqual(parent(), 'rejected')
            p.write_text('good')
            self.assertEqual(parent(), 'good')

    def test_metadata_scope_changes_do_not_invalidate_but_replacement_does(self):
        from grading import image_manifest
        from grading.metadata_admission import metadata_validation_cache
        calls = []
        with ReadSession({}) as session:
            @session.memoize
            def gate():
                calls.append(1); return 'passed'
            with metadata_validation_cache(): gate()
            with metadata_validation_cache(): gate()
            gate()
            self.assertEqual(len(calls), 1)
            with patch.object(image_manifest, 'validate_record', lambda *a: None):
                gate()
            self.assertEqual(len(calls), 2)

    def test_nested_misses_validate_union_once_and_do_not_publish_after_mutation(self):
        from read_session import Reads
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            path = Path(tmp) / 'file'; path.write_text('good')
            calls = []
            original = Reads.current
            def current(reads):
                calls.append(1); return original(reads)
            @session.memoize
            def child(): return path.read_text()
            @session.memoize
            def parent(): return child()
            with patch.object(Reads, 'current', current):
                self.assertEqual(parent(), 'good')
                self.assertEqual(len(calls), 1)
                self.assertEqual(child(), 'good')
            @session.memoize
            def changed_child(): return path.read_text()
            @session.memoize
            def mutator():
                value = changed_child()
                path.write_text('evil')
                return value
            self.assertEqual(mutator(), 'good')
            self.assertEqual(changed_child(), 'evil')
            self.assertFalse(session.pending)

    def test_shared_bytes_reuse_keeps_ctime_and_symlink_guards(self):
        import read_session
        with tempfile.TemporaryDirectory() as tmp, ReadSession({}) as session:
            root = Path(tmp); p = root / 'a'; p.write_bytes(b'good')
            link = root / 'link'; link.symlink_to(p)
            @session.memoize
            def first(): return p.read_bytes()
            @session.memoize
            def second(): return link.read_bytes()
            with patch.object(read_session, 'buffer_read', wraps=read_session.buffer_read) as io:
                self.assertEqual(first(), b'good'); self.assertEqual(second(), b'good')
                self.assertEqual(io.call_count, 1)
                stamp = p.stat(); p.write_bytes(b'evil')
                os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                self.assertEqual(second(), b'evil')
                self.assertEqual(io.call_count, 2)
                other = root / 'b'; other.write_bytes(b'next')
                link.unlink(); link.symlink_to(other)
                self.assertEqual(second(), b'next')
                self.assertEqual(io.call_count, 3)

    def test_manifest_scope_tokens_do_not_force_a_new_load_per_case(self):
        from grading import image_manifest
        from grading.metadata_admission import metadata_validation_cache
        calls = []
        def load(language):
            calls.append(1); return {'language': language}
        with patch.object(image_manifest, 'load', load), ReadSession({'grading.image_manifest': ('load',)}) as session:
            for _ in range(3):
                with metadata_validation_cache():
                    self.assertEqual(image_manifest.load('cpp'), {'language': 'cpp'})
            self.assertEqual(calls, [1])
            self.assertEqual(session.hits, 2)

    def test_new_startup_does_not_inherit_previous_admission(self):
        calls = []
        def original(): calls.append(1); return 'ok'
        for _ in range(2):
            with ReadSession({}) as session:
                gate = session.memoize(original)
                self.assertEqual(gate(), 'ok'); self.assertEqual(gate(), 'ok')
                self.assertEqual(session.misses, 1)
                self.assertEqual(session.hits, 1)
        self.assertEqual(len(calls), 2)

    def test_merkle_input_hit_still_rejects_same_size_blob_corruption(self):
        import action_cache
        import cache
        with tempfile.TemporaryDirectory() as tmp:
            local = action_cache.Cache(tmp)
            action = action_cache.Action('guard-test', {'sub/input': b'good'})
            local.publish(action)
            def init(instance, root=tmp): instance.root = Path(root)
            with patch.object(action_cache.Cache, '__init__', init), \
                 ReadSession({'cache': ('action_input_bytes',)}) as session:
                self.assertEqual(cache.action_input_bytes(action.descriptor, 'sub/input'), b'good')
                self.assertEqual(cache.action_input_bytes(action.descriptor, 'sub/input'), b'good')
                self.assertEqual(session.hits, 1)
                path = Path(tmp) / 'cas' / action_cache.digest(b'good')['hash']
                stamp = path.stat(); path.write_bytes(b'evil')
                os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                with self.assertRaises(ValueError): cache.action_input_bytes(action.descriptor, 'sub/input')
                self.assertEqual(session.invalidations, 1)


if __name__ == '__main__': unittest.main()
