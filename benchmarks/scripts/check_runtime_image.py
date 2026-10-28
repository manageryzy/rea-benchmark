"""Focused offline image/cache controls. No benchmark or model entrypoint."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import runtime_image as image
import runtime_storage as storage


class Images(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        tree = self.root/'input'; tree.mkdir()
        (tree/'hello').write_text('public fixture')
        manifest = storage.canonical({'fixture': 'public'})
        (tree/'manifest.json').write_bytes(manifest)
        self.cache = self.root/'cache'; self.cache.mkdir()
        candidate = self.cache/'candidate.squashfs'
        subprocess.run(['mksquashfs',str(tree),str(candidate),'-noappend','-no-progress',
            '-processors','1'],check=True,stdout=subprocess.DEVNULL)
        identity = storage.sha_file(candidate)
        self.path = self.cache/(identity+'.squashfs'); candidate.rename(self.path)
        self.record = {'image_sha256':identity, 'manifest_sha256':hashlib.sha256(manifest).hexdigest()}

    def test_attestation_roots_follow_shared_policy_and_report_all_missing(self):
        root = self.root/'workspace'; (root/'benchmarks/config').mkdir(parents=True)
        present = Path('/usr/bin/expr')
        absent = Path('/usr/bin/rea-missing-command-fixture'); other = Path('/usr/lib/rea-missing-library-fixture')
        path = root/'benchmarks/config/runtime-attestation.json'
        path.write_text(json.dumps({'components': {'shell': {'paths': [str(present), 'bin/rea']}}}))
        self.assertEqual(image.attestation_roots(root), [str(present)])
        path.write_text(json.dumps({'components': {'shell': {'paths': [str(absent), str(other)]}}}))
        with self.assertRaises(RuntimeError) as error: image.attestation_roots(root)
        self.assertIn(str(absent), str(error.exception)); self.assertIn(str(other), str(error.exception))
        path.write_text(json.dumps({'components': {'bad': {'paths': [str(self.root/'credentials')]}}}))
        with self.assertRaisesRegex(ValueError, 'reviewed public system roots'):
            image.attestation_roots(root)

    def test_full_acceptance_checks_workspace_and_system_paths(self):
        import runtime_image_acceptance as acceptance
        root = self.root/'workspace'; (root/'benchmarks/config').mkdir(parents=True)
        (root/'benchmarks/config/runtime-attestation.json').write_text(json.dumps({
            'components': {'launch': {'paths': ['/usr/bin/expr', 'bin/rea']}}}))
        self.assertEqual(acceptance.required_paths(root), sorted(['/usr/bin/expr', str(root/'bin/rea')]))
        with self.assertRaisesRegex(RuntimeError, 'bin/rea'):
            acceptance.check_paths(acceptance.required_paths(root))

    def test_system_config_mounts_are_readonly_and_confined(self):
        root = self.root/'workspace'; root.mkdir()
        work = self.root/'work'; work.mkdir()
        record = {'manifest': {'workspace_paths': [], 'toolchain': {
            'rust_sysroot': '/opt/rust', 'system_mounts': ['etc/ld.so.cache']}}}
        cmd = image.container_command(self.root/'tree', record, work, root, ['true'])
        target = cmd.index('/etc/ld.so.cache')
        self.assertEqual(cmd[target-2], '--ro-bind')
        work = self.root/'other-work'; work.mkdir()
        record['manifest']['toolchain']['system_mounts'] = ['etc/../private']
        with self.assertRaisesRegex(ValueError, 'Unexpected system'):
            image.container_command(self.root/'tree', record, work, root, ['true'])

    def test_loader_alias_preserves_approved_canonical_library_identity(self):
        name = 'usr/lib64/ld-linux-x86-64.so.2'
        target = 'usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'
        file = self.root/name; file.parent.mkdir(parents=True); file.write_bytes(b'ELF')
        entry = {'sha256': 'a', 'source_link': '../lib/loader', 'resolved_link': 'system/'+target}
        files = {name: entry, target: {'sha256': 'a'}}; aliases = {}
        image.preserve_loader_aliases(self.root, files, aliases)
        self.assertEqual(aliases, {name: '/'+target}); self.assertNotIn(name, files)
        self.assertFalse(file.exists())
        with self.assertRaisesRegex(ValueError, 'Missing canonical loader'):
            image.preserve_loader_aliases(self.root, {name: entry}, {})

    def test_real_loader_capture_includes_canonical_target(self):
        from grading.toolchains import Builder
        builder = Builder(); builder.allow('/usr', '/lib', '/lib64')
        loader = Path('/lib64/ld-linux-x86-64.so.2')
        builder.add(loader); builder.add(loader.resolve())
        destination = self.root/'system'; destination.mkdir()
        files = image.copy_system_files(builder, destination); aliases = {}
        image.preserve_loader_aliases(destination, files, aliases)
        self.assertEqual(aliases['usr/lib64/ld-linux-x86-64.so.2'], str(loader.resolve()))
        self.assertIn(str(loader.resolve()).lstrip('/'), files)

    def test_hash_memo_and_same_size_mutation_with_restored_mtime(self):
        stamp = self.cache/'verified.json'; identity = self.record['image_sha256']
        image.verified_image(self.path,identity,stamp)
        with patch.object(storage,'sha_file',side_effect=AssertionError('rehash')):
            image.verified_image(self.path,identity,stamp)
        previous = self.path.stat()
        raw = self.path.read_bytes()
        self.path.write_bytes(bytes([raw[0]^1])+raw[1:])
        os.utime(self.path,ns=(previous.st_atime_ns,previous.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'corrupt'):
            image.verified_image(self.path,identity,stamp)

    def test_fallback_extracts_once_then_reuses_without_process(self):
        with patch.object(image,'fuse_available',return_value=False):
            with image.image_root(self.record,self.cache,self.root) as (tree,backend):
                self.assertEqual(backend,'extract'); self.assertEqual((tree/'hello').read_text(),'public fixture')
            with patch.object(subprocess,'run',side_effect=AssertionError('duplicate extraction')):
                with image.image_root(self.record,self.cache,self.root) as (again,backend):
                    self.assertEqual(again,tree)
        self.assertFalse(list(self.cache.glob('extract-*')))

    def test_missing_fuse_explicit_backend_fails(self):
        with patch.object(image,'fuse_available',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'FUSE requested'):
                with image.image_root(self.record,self.cache,self.root,'fuse'): pass

    def test_mount_failure_falls_back_but_task_failure_does_not(self):
        from contextlib import contextmanager
        @contextmanager
        def failing(*_): raise OSError('fixture mount failure'); yield
        with patch.object(image,'fuse_available',return_value=True),patch.object(image,'fuse_mount',failing):
            with image.image_root(self.record,self.cache,self.root) as (_,backend):self.assertEqual(backend,'extract')
        @contextmanager
        def mounted(*_): yield self.root/'input'
        with patch.object(image,'fuse_available',return_value=True),patch.object(image,'fuse_mount',mounted),patch.object(image,'extracted_image',side_effect=AssertionError('reran task')):
            with self.assertRaisesRegex(OSError,'task failed'):
                with image.image_root(self.record,self.cache,self.root): raise OSError('task failed')

    def test_replaced_tree_and_symlink_rejected(self):
        tree = image.extracted_image(self.record,self.cache)
        tree.rename(self.cache/'saved'); tree.mkdir()
        with self.assertRaisesRegex(ValueError,'identity changed'): image.extracted_image(self.record,self.cache)
        tree.rmdir(); tree.symlink_to(self.cache/'saved')
        with self.assertRaisesRegex(ValueError,'Symlink'): image.extracted_image(self.record,self.cache)

    def test_real_fuse_mount_interrupt_cleanup(self):
        if not image.fuse_available(): self.skipTest('FUSE device not exposed')
        with self.assertRaises(KeyboardInterrupt):
            with image.fuse_mount(self.path,self.root) as tree:
                self.assertTrue(os.path.ismount(tree))
                self.assertEqual((tree/'hello').read_text(),'public fixture')
                output=subprocess.check_output(['bwrap','--unshare-user','--ro-bind','/','/',
                    '--ro-bind',str(tree),str(tree),'/usr/bin/cat',str(tree/'hello')])
                self.assertEqual(output,b'public fixture')
                with self.assertRaises(OSError): (tree/'hello').write_text('no')
                raise KeyboardInterrupt
        self.assertFalse((self.root/'mount').exists())

    def test_manifest_mismatch_rejected_on_cached_tree(self):
        tree=image.extracted_image(self.record,self.cache)
        record={**self.record,'manifest_sha256':'0'*64}
        with patch.object(image,'fuse_available',return_value=False):
            with self.assertRaisesRegex(ValueError,'not bound'):
                with image.image_root(record,self.cache,self.root):pass

    def test_refresh_reuses_image_even_after_legacy_tar_retired(self):
        class CaptureReached(Exception): pass
        (self.cache/'active.json').write_text('{}')
        previous={**self.record,'manifest':{'runtime_archive_sha256':'a'*64,'workspace_paths':['node_modules']}}
        with patch.object(image,'cache_root',return_value=self.cache), patch.object(image,'load',return_value=previous), \
                patch.object(storage,'package',side_effect=AssertionError('retired tar accessed')), \
                patch.object(image,'capture_system',side_effect=CaptureReached):
            with self.assertRaises(CaptureReached):image.build(self.root,refresh=True,reuse_runtime=True)
        self.assertFalse(list(self.cache.glob('build-*')))

    def test_prune_requires_smoke_and_removes_only_exact_replaced_tar(self):
        runtime=self.root/'.cache/benchmark-runtime'; store=runtime/'installed-runtime'; store.mkdir(parents=True)
        raw=b'replaced archive'; identity=hashlib.sha256(raw).hexdigest()
        old=store/(identity+'.tar'); old.write_bytes(raw)
        keep=store/'unrelated.tar';keep.write_bytes(b'keep')
        record={**self.record,'manifest':{'runtime_archive_sha256':identity}}
        with patch.object(image,'cache_root',return_value=self.cache),patch.object(image,'load',return_value=record):
            with self.assertRaises(RuntimeError):image.prune_legacy(self.root)
            (self.cache/(self.record['image_sha256']+'.smoke.json')).write_text(json.dumps({'image_sha256':self.record['image_sha256'],'model_api_calls':0,'passed':True}))
            self.assertEqual(image.prune_legacy(self.root),len(raw))
        self.assertTrue(keep.exists());self.assertFalse(old.exists())

if __name__=='__main__':unittest.main()
