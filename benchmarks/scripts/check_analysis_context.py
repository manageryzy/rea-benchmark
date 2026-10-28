"""Offline public-entry analysis-context controls; never invokes a model."""
import copy
import argparse
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import action_cache
import cache
from capabilities import analysis_context as context


class AnalysisContextControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = action_cache.ROOT
        index = root / '.cache/benchmark-capabilities/task-index/record_codec_sysv_amd64_scalar_recover.json'
        cls.case = json.loads(index.read_bytes())
        cls.directory = root / cls.case['public_directory']
        cls.raw = (cls.directory / 'target').read_bytes()
        cls.manifest = json.loads((cls.directory / 'manifest.json').read_bytes())
        cls.context = context.derive(cls.raw, cls.manifest, 'abi_probe')

    def test_01_exact_public_entries_match_the_observed_elf_mapping(self):
        self.assertEqual(self.context['load_base'], 0x1000000)
        self.assertEqual(self.context['entry_addresses'], {'original_callee': 0x1000090, 'original_caller': 0x10001b0})
        self.assertEqual(self.context['seed_text'], '0x1000090\tfunction\n0x10001b0\tfunction\n')
        self.assertEqual(self.context['seed_sha256'], '3b1aaaff86d519fb17acd3cab8718b69135d3d923f1e62855466db17748b7cef')

    def test_02_every_frozen_package_uses_only_its_disclosed_entries(self):
        seeded = hint_free = 0
        for index in (action_cache.ROOT / '.cache/benchmark-capabilities/task-index').glob('*.json'):
            case = json.loads(index.read_bytes())
            directory = action_cache.ROOT / case['public_directory']
            manifest = json.loads((directory / 'manifest.json').read_bytes())
            value = context.derive((directory / 'target').read_bytes(), manifest, case['kind'])
            if case['kind'] in context.HINT_FREE:
                self.assertIsNone(value)
                hint_free += 1
            else:
                entries = manifest.get('entry_points', {'target': manifest.get('entry_rva')})
                self.assertEqual(set(value['entry_addresses']), set(entries))
                self.assertEqual({address - value['load_base'] for address in value['entry_addresses'].values()}, set(entries.values()))
                self.assertTrue(all(line.endswith('\tfunction') and len(line.split('\t')) == 2
                                    for line in value['seed_text'].splitlines()))
                seeded += 1
        self.assertEqual((seeded, hint_free), (144, 9))

    def test_03_policy_is_explicit_and_strictly_boolean(self):
        self.assertFalse(context.enabled({}))
        self.assertFalse(context.enabled({'known_entry_seeds': False}))
        self.assertTrue(context.enabled({'known_entry_seeds': True}))
        for value in (0, 1, None, 'true'):
            with self.assertRaises(ValueError): context.enabled({'known_entry_seeds': value})

    def test_04_localization_cannot_acquire_seed_hints(self):
        for kind in context.HINT_FREE:
            with self.assertRaises(ValueError): context.derive(self.raw, self.manifest, kind)
            self.assertIsNone(context.derive(self.raw, {}, kind))

    def test_05_wrong_binary_binding_is_rejected(self):
        with self.assertRaises(ValueError): context.derive(self.raw + b'x', self.manifest, 'abi_probe')

    def test_06_malformed_headers_are_rejected(self):
        for raw in (self.raw[:63], b'not ELF', self.raw[:4] + b'\x01' + self.raw[5:]):
            with self.assertRaises(ValueError): context.load_segments(raw)
        raw = bytearray(self.raw); struct.pack_into('<Q', raw, 32, len(raw))
        with self.assertRaises(ValueError): context.load_segments(raw)

    def test_07_invalid_or_nonexecutable_entries_are_rejected(self):
        for rva in (-1, True, 2**64, 0x1000, 0x2000, 0x100000):
            manifest = {**self.manifest, 'entry_points': {'original_caller': rva}}
            with self.assertRaises(ValueError): context.derive(self.raw, manifest, 'abi_probe')

    def test_08_entry_contract_is_unambiguous(self):
        for manifest in ({**self.manifest, 'entry_rva': 0x90},
                         {**self.manifest, 'entry_points': {}},
                         {**self.manifest, 'entry_points': [0x90]}):
            with self.assertRaises(ValueError): context.derive(self.raw, manifest, 'abi_probe')

    def test_09_environment_requires_exact_regular_seed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); path = directory / context.FILENAME
            path.write_text(self.context['seed_text'])
            self.assertEqual(context.environment(self.context, directory), {'REA_GHIDRA_SEED_FILE': str(path)})
            path.write_text('0x1000000\tfunction\tprivate_name\n')
            with self.assertRaises(ValueError): context.environment(self.context, directory)
            path.unlink(); path.symlink_to(self.directory / 'target')
            with self.assertRaises(ValueError): context.environment(self.context, directory)

    def materialized(self, directory):
        directory.joinpath('target').write_bytes(self.raw)
        directory.joinpath('manifest.json').write_text(json.dumps(self.manifest))
        directory.joinpath(context.FILENAME).write_text(self.context['seed_text'])
        task = {'input_directory': str(directory), 'kind': 'abi_probe'}
        config = {'gateway': {'analysis_context': self.context}, 'harness': {'known_entry_seeds': True}}
        return task, config

    def test_10_configuration_is_rederived_from_public_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, config = self.materialized(Path(temporary))
            self.assertEqual(context.verify_materialized(task, config), self.context)
            config = copy.deepcopy(config); config['gateway']['analysis_context']['entry_addresses']['original_caller'] += 1
            with self.assertRaises(ValueError): context.verify_materialized(task, config)

    def test_11_premises_only_has_no_seed_file_or_backend_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, config = self.materialized(Path(temporary))
            task['evidence_access'] = 'premises_only'; config['gateway'].clear()
            Path(temporary, 'target').write_bytes(b'')
            with self.assertRaises(ValueError): context.verify_materialized(task, config)
            Path(temporary, context.FILENAME).unlink()
            self.assertIsNone(context.verify_materialized(task, config))

    def test_12_unknown_task_kinds_are_rejected(self):
        with self.assertRaises(ValueError): context.derive(self.raw, self.manifest, 'unknown')

    def test_13_unbound_seed_files_cannot_be_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            task, config = self.materialized(Path(temporary)); config = {'gateway': {}, 'harness': {}}
            with self.assertRaises(ValueError): context.verify_materialized(task, config)
            Path(temporary, context.FILENAME).unlink()
            self.assertIsNone(context.verify_materialized(task, config))

    def test_14_prompt_contains_public_absolute_addresses(self):
        text = context.prompt(self.context)
        self.assertIn(b'0x10001b0', text)
        self.assertIn(b'0x1000090', text)
        self.assertNotIn(b'delta', text)
        self.assertEqual(context.prompt(None), b'')

    def test_15_mapping_order_does_not_change_seed_content(self):
        manifest = {**self.manifest, 'entry_points': dict(reversed(list(self.manifest['entry_points'].items())))}
        self.assertEqual(context.derive(self.raw, manifest, 'abi_probe'), self.context)

    def test_16_seed_descriptor_cannot_choose_another_path(self):
        value = {**self.context, 'seed_file': '../unrelated'}
        with self.assertRaises(ValueError): context.environment(value, '/tmp')
        with tempfile.TemporaryDirectory() as temporary, patch.object(context, 'RECEIPT', Path(temporary) / 'gate.json'):
            with self.assertRaises(RuntimeError): context.require_validation()
            context.RECEIPT.write_text(json.dumps({'schema_version': 1, 'action': {}, 'validation_id': '0' * 64}))
            with self.assertRaises(ValueError): context.require_validation()

    def test_17_merkle_reader_handles_repeated_path_names(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = action_cache.Cache(temporary)
            action = action_cache.Action('input_reader_control', {'task/task': b'committed seed'})
            store.publish(action)
            self.assertEqual(cache.action_input_bytes(action.descriptor, 'task/task', read_blob=store.get), b'committed seed')
            with self.assertRaises(ValueError): cache.action_input_bytes(action.descriptor, 'task/missing', read_blob=store.get)

    def test_18_merkle_reader_rejects_changed_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = action_cache.Cache(temporary); action = action_cache.Action('input_reader_control', {'seed': b'original'})
            store.publish(action)
            def changed(value):
                raw = store.get(value)
                return raw + b'changed'
            with self.assertRaises(ValueError): cache.action_input_bytes(action.descriptor, 'seed', read_blob=changed)

    def test_19_merkle_reader_rejects_duplicate_or_invalid_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = action_cache.Cache(temporary); leaf = store.put(b'seed')
            for files in ([{'name': 'seed', 'digest': leaf, 'is_executable': False}] * 2,
                          [{'name': '../seed', 'digest': leaf, 'is_executable': False}],
                          [{'name': 'seed', 'digest': leaf, 'is_executable': 1}]):
                tree = store.put(action_cache.canonical({'directories': [], 'files': files}))
                with self.assertRaises(ValueError): cache.action_input_bytes({'input_root_digest': tree}, 'seed', read_blob=store.get)

    def test_20_seed_configuration_changes_are_independent_of_model_prices(self):
        before = copy.deepcopy(self.context)
        manifest = {**self.manifest, 'display_label': 'changed', 'price_note': 'unrelated report metadata'}
        self.assertEqual(context.derive(self.raw, manifest, 'abi_probe'), before)
        self.assertNotIn('model', before)
        self.assertNotIn('price', before)

    def test_21_selected_catalog_does_not_open_unrelated_packages(self):
        original = Path.read_bytes
        seen = []
        task_id = self.case['id']
        allowed = {context.ROOT / self.case['public_directory'] / name
                   for name in ('target', 'prompt.txt', 'manifest.json', 'tools.json')}
        allowed.add(context.ROOT / '.cache/benchmark-capabilities/task-index' / (task_id + '.json'))
        def read(path):
            self.assertIn(path, allowed)
            seen.append(path)
            return original(path)
        with patch.object(Path, 'read_bytes', read):
            selected = context.public_catalog([task_id])
        self.assertEqual(set(selected), {task_id})
        self.assertEqual(set(seen), allowed)
        with self.assertRaises(ValueError): context.public_catalog(['../../private'])

    def test_22_selected_catalog_still_rejects_changed_public_bytes(self):
        original = Path.read_bytes
        def read(path):
            raw = original(path)
            return raw + b'changed' if path == self.directory / 'target' else raw
        with patch.object(Path, 'read_bytes', read), self.assertRaisesRegex(ValueError, 'package changed'):
            context.public_catalog([self.case['id']])


DEPENDENCY_ROOT = None


def tool_inventory(*, fresh=False):
    import runtime_attestation
    policy = copy.deepcopy(runtime_attestation.config())
    if DEPENDENCY_ROOT is not None:
        for spec in policy['components'].values():
            for name in spec['paths']:
                if not Path(name).is_absolute():
                    policy['approved_system_roots'].append(str((DEPENDENCY_ROOT / name).resolve()))
    names = {'benchmark-tool-surface', 'rea-and-mcp-dependencies', 'analysis-node',
             'ghidra', 'java', 'launchers', 'shell-and-tls'}
    for name, spec in policy['components'].items():
        spec['harnesses'] = ['analysis-context-control'] if name in names else []
    value = runtime_attestation.capture('analysis-context-control', policy=policy, fresh=fresh)
    store = action_cache.Cache()
    components = {name: item for name, item in value['manifest']['components'].items() if name != 'attestation-control'}
    summaries = {name: {key: item[key] for key in ('sha256', 'files', 'bytes')} for name, item in components.items()}
    entries = {name: runtime_attestation.component_entries(item, store.get) for name, item in components.items()}
    identity = {key: value['manifest'][key] for key in ('environment', 'platform', 'network')}
    identity['components'] = summaries
    return identity, entries


def gateway_action(identity, entries):
    import run
    public = AnalysisContextControls.directory
    raw = AnalysisContextControls.raw
    descriptor = AnalysisContextControls.context
    tools = run.paged_tools((public / 'tools.json').read_bytes())
    runtime_environment = {**identity['environment'], 'REA_GHIDRA_SEED_FILE': '@fixture/analysis.seeds'}
    gateway = {'max_tool_calls': 3, 'tool_timeout_ms': 120000, 'analysis_tools_file': '@fixture/tools.json',
               'data_limits': {}, 'evidence_paging': json.loads((context.ROOT / 'benchmarks/config/evidence-paging.json').read_bytes()),
               'analysis_context': descriptor, 'runtime_environment': runtime_environment}
    inputs = {'target': raw, 'analysis.seeds': descriptor['seed_text'].encode('ascii'),
              'tools.json': tools, 'gateway.json': action_cache.canonical(gateway),
              'runtime/identity.json': action_cache.canonical(identity)}
    for name in ('analysis-context-smoke.mjs', 'rea-gateway.mjs', 'tool-catalog.mjs', 'evidence-paging.mjs'):
        relative = 'benchmarks/scripts/' + name
        inputs['sources/' + relative] = (context.ROOT / relative).read_bytes()
    for name, component in entries.items():
        inputs['runtime/components/' + name + '.json'] = action_cache.canonical(component)
    action = action_cache.Action('public_entry_gateway_smoke_v1', inputs,
        arguments=['rea-benchmark', 'validate-provided-entry-context'],
        platform={'system': identity['platform'], 'scope': 'local-static-analysis-only'}, timeout_seconds=300)
    return action, inputs, gateway


def expand(value, directory):
    if isinstance(value, dict): return {key: expand(item, directory) for key, item in value.items()}
    if isinstance(value, list): return [expand(item, directory) for item in value]
    if isinstance(value, str): return value.replace('@fixture', str(directory)).replace('@workspace', str(context.ROOT))
    return value


def run_gateway(action, inputs, gateway, identity):
    import os
    import signal
    import subprocess
    runtime = context.ROOT / '.cache/benchmark-runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='analysis-context-control-', dir=runtime))
    for name in ('target', 'analysis.seeds', 'tools.json'):
        (directory / name).write_bytes(inputs[name])
    (directory / 'gateway.json').write_bytes(action_cache.canonical(expand(gateway, directory)))
    spec = {'target': str(directory / 'target'), 'gateway': str(directory / 'gateway.json'),
            'trace': str(directory / 'tool-calls.jsonl'),
            'address': hex(AnalysisContextControls.context['entry_addresses']['original_caller']),
            'seed_sha256': context.sha(inputs['analysis.seeds']), 'output': str(directory / 'result.json')}
    (directory / 'spec.json').write_bytes(action_cache.canonical(spec))
    command = [str(context.ROOT / '.tools/node-v24.22.0-linux-x64/bin/node'),
               str(context.ROOT / 'benchmarks/scripts/analysis-context-smoke.mjs'), str(directory / 'spec.json')]
    with (directory / 'stdout.log').open('wb') as stdout, (directory / 'stderr.log').open('wb') as stderr:
        child = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True,
                                 env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        try: code = child.wait(timeout=300)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try: code = child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL); code = child.wait()
    if code != 0:
        raise RuntimeError('Actual analysis gateway failed; private diagnostics: ' + str(directory))
    for name in ('target', 'analysis.seeds', 'tools.json'):
        if (directory / name).read_bytes() != inputs[name]:
            raise ValueError('Analysis gateway mutated a declared input')
    after, _ = tool_inventory(fresh=True)
    if after != identity:
        raise ValueError('Analysis tool runtime changed during the gateway control')
    value = json.loads((directory / 'result.json').read_bytes())
    if value.get('passed') is not True or value.get('model_api_calls') != 0:
        raise ValueError('Analysis gateway control did not pass')
    saved = action_cache.Cache().capture(action, directory, names=['result.json', 'tool-calls.jsonl',
        'evidence-01-open_binary.json', 'evidence-02-procedure_info.json', 'evidence-03-close_binary.json'])
    return saved


def main():
    global DEPENDENCY_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-root', type=Path, help='Exact configured dependency roots for an isolated offline source snapshot')
    args = parser.parse_args()
    DEPENDENCY_ROOT = args.dependency_root.resolve(strict=True) if args.dependency_root else None
    sources = context.gate_fingerprints()
    catalog = context.public_catalog()
    context.RECEIPT.write_text(json.dumps({'schema_version': 1, 'status': 'validation_in_progress', 'model_api_calls': 0})+'\n')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AnalysisContextControls))
    if not result.wasSuccessful(): return 1
    print('Hashing the reviewed analysis-only tool closure; no model clients', flush=True)
    identity, entries = tool_inventory()
    action, inputs, gateway = gateway_action(identity, entries)
    store = action_cache.Cache()
    saved = store.lookup(action)
    hit = saved is not None
    if not hit:
        print('Running the actual local MCP/Ghidra gateway control; no model calls', flush=True)
        saved = run_gateway(action, inputs, gateway, identity)
    if sources != context.gate_fingerprints() or catalog != context.public_catalog():
        raise ValueError('Analysis-context sources or public packages changed during validation')
    components = {name: item['sha256'] for name, item in identity['components'].items() if name != 'native-loader-closure'}
    engine = {'action': action.descriptor, 'tool_components': components, 'native_entries': entries['native-loader-closure']}
    validation_inputs = {'catalog.json': action_cache.canonical(catalog), 'engine.json': action_cache.canonical(engine)}
    validation_inputs.update({'sources/' + name: (context.ROOT / name).read_bytes() for name in sources})
    validation = action_cache.Action('analysis_context_validation_v1', validation_inputs,
        arguments=['rea-benchmark', 'validate-analysis-context-controls'],
        platform={'scope': 'source-bound-offline-controls'}, timeout_seconds=600)
    record = {'schema_version': 1, 'passed': True, 'checks': result.testsRun, 'model_api_calls': 0,
              'inputs_sha256': sources, 'engine_action': action.descriptor,
              'tool_components': components, 'native_entries': entries['native-loader-closure']}
    store.commit(validation, {'validation.json': action_cache.canonical(record)})
    selected = {'schema_version': 1, 'validation_id': validation.key, 'action': validation.descriptor}
    context.RECEIPT.write_bytes(action_cache.canonical(selected))
    context.require_validation()
    print(json.dumps({'passed': True, 'checks': result.testsRun, 'model_api_calls': 0,
                      'engine_cache_hit': hit, 'engine_action': action.key, 'validation_id': validation.key}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
