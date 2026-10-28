"""Explicit historical-baseline plus current delta acceptance; never 104 new passes.

The reviewed policy pins every changed input. Unknown changes fail closed. The
old AC/CAS proofs stay immutable, and focused results have their own action.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import action_cache as ac
import validation_cache as vc

POLICY = 'benchmarks/config/incremental-validation.json'
RECEIPT = 'benchmarks/config/incremental-validation-result.json'
KIND = 'coordinator-baseline-plus-delta-v1'


def object_at(cache, key):
    path = cache.root / 'cas' / key
    if path.is_symlink():
        raise ValueError('Historical proof is a symlink')
    raw = cache.get({'hash': key, 'size_bytes': path.stat().st_size})
    if hashlib.sha256(raw).hexdigest() != key:
        raise ValueError('Historical proof digest mismatch')
    return json.loads(raw)


def leaves(cache, digest, prefix=''):
    node = json.loads(cache.get(digest))
    result = {prefix + row['name']: row['digest'] for row in node['files']}
    for row in node['directories']:
        result.update(leaves(cache, row['digest'], prefix + row['name'] + '/'))
    return result


def baseline(cache, key):
    receipt = object_at(cache, key)
    if (receipt.get('schema_version') != 2 or receipt.get('passed') is not True
            or receipt.get('failed') != 0 or receipt.get('provider_launches') != 0
            or receipt.get('model_api_calls') != 0
            or len(receipt.get('actions', {})) != receipt.get('checks')):
        raise ValueError('Invalid historical baseline')
    common = None
    codes = {}
    for name, action_key in receipt['actions'].items():
        descriptor = object_at(cache, action_key)
        result = cache.lookup(SimpleNamespace(key=action_key, descriptor=descriptor))
        if result is None or set(result['output_files']) != {'check.json'}:
            raise ValueError('Missing historical check proof: ' + name)
        row = json.loads(cache.get(result['output_files']['check.json']['digest']))
        if (row.get('test') != name or row.get('action') != action_key
                or row.get('passed') is not True or row.get('provider_launches') != 0
                or row.get('model_api_calls') != 0):
            raise ValueError('Invalid historical check proof: ' + name)
        inputs = leaves(cache, descriptor['input_root_digest'])
        dependency = json.loads(cache.get(inputs.pop('dependencies.action.json')))
        if common is not None and dependency != common:
            raise ValueError('Historical checks use different dependency baselines')
        common = dependency
        codes[name] = inputs
    return receipt, common, codes


def compare_inputs(old, current, reviewed):
    changes = {name: {'before': old.get(name), 'after': current.get(name)}
               for name in sorted(set(old) | set(current)) if old.get(name) != current.get(name)}
    if changes != reviewed:
        raise RuntimeError('Incremental validation input delta differs from reviewed policy')
    return changes


def check_platform(before, current, reviewed):
    if before == current:
        return
    if not any(row.get('before') == before and row.get('after') == current for row in reviewed):
        raise RuntimeError('Historical validation toolchain changed')


def build(plan, cache=None):
    cache = cache or ac.Cache()
    raw = vc.read_file(plan.root / POLICY)
    policy = json.loads(raw)
    if policy.get('schema_version') != 1 or policy.get('mode') != KIND:
        raise ValueError('Invalid incremental validation policy')
    historical, common, codes = baseline(cache, policy['baseline_receipt_sha256'])
    if set(historical['actions']) != set(plan.tests):
        raise RuntimeError('Coordinator check catalogue changed')
    command = json.loads(cache.get(common['command_digest']))
    check_platform(command['platform'], plan.platform, policy.get('reviewed_platform_transitions', []))
    before = leaves(cache, common['input_root_digest'])
    # Resolve the current Merkle tree locally, without publishing new passes.
    class Local:
        def get(self, value):
            data = plan.common.blobs[value['hash']]
            if ac.digest(data) != value:
                raise ValueError('Current dependency digest mismatch')
            return data
    after = leaves(Local(), plan.common.descriptor['input_root_digest'])
    changes = compare_inputs(before, after, policy['reviewed_changes'])
    test_changes = {}
    for name, inputs in codes.items():
        current = {p: ac.digest(b) for p, b in plan.code[name].items()}
        if current != inputs:
            test_changes[name] = {'before': inputs, 'after': current}
    if test_changes != policy.get('reviewed_test_changes', {}):
        raise RuntimeError('Test body/helper delta differs from reviewed policy')
    extra = {name: vc.read_file(plan.root / name) for name in policy['additional_inputs']}
    selected = policy['coordinator_checks']
    if (not selected or not set(selected).issubset(plan.tests)
            or not set(test_changes).issubset(selected)):
        raise ValueError('Invalid focused coordinator selection')
    inputs = {**extra, POLICY: raw,
        'current-dependencies.json': ac.canonical(plan.common.descriptor),
        'historical-receipt.json': ac.canonical(historical),
        'current-focused-actions.json': ac.canonical({n: plan.actions[n].key for n in selected})}
    action = ac.Action(KIND, inputs, arguments=policy['focused_test_classes'],
        platform=plan.platform, environment={'provider_network': 'forbidden', 'model_launch': 'forbidden'})
    return action, policy, changes


def require(plan, cache=None):
    cache = cache or ac.Cache()
    action, policy, _ = build(plan, cache)
    result = cache.lookup(action)
    if result is None or set(result['output_files']) != {'validation.json'}:
        raise RuntimeError('Run make check-capability-incremental for the reviewed input delta')
    value = json.loads(cache.get(result['output_files']['validation.json']['digest']))
    if (value.get('passed') is not True or value.get('kind') != KIND
            or value.get('model_api_calls') != 0 or value.get('provider_launches') != 0
            or value.get('focused_checks') != policy['expected_focused_checks']
            or value.get('coordinator_checks') != len(policy['coordinator_checks'])
            or value.get('historical_checks') != len(plan.tests)):
        raise ValueError('Invalid incremental acceptance result')
    for name in policy['coordinator_checks']:
        if plan.lookup(cache, name) is None:
            raise RuntimeError('Missing current focused coordinator proof: ' + name)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    from capabilities import evidence
    from check_capability_coordinator import TypedCoordinatorControls
    names = unittest.defaultTestLoader.getTestCaseNames(TypedCoordinatorControls)
    plan = vc.Plan(ac.ROOT, evidence.SOURCES, names)
    cache = ac.Cache()
    action, policy, changes = build(plan, cache)
    if cache.lookup(action) is not None:
        print(json.dumps(require(plan, cache)), flush=True)
        return
    print(f'INCREMENTAL: {len(names)} historical passes retained; {len(changes)} reviewed input changes', flush=True)
    import run
    suite = unittest.TestSuite()
    for dotted in policy['focused_test_classes']:
        module, cls = dotted.rsplit('.', 1)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(getattr(importlib.import_module(module), cls)))
    if suite.countTestCases() != policy['expected_focused_checks']:
        raise ValueError('Focused check catalogue differs from policy')
    with (patch.object(socket.socket, 'connect', side_effect=AssertionError('Provider network forbidden')),
          patch.object(run, 'launch_model', side_effect=AssertionError('Provider launch forbidden'))):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or result.expectedFailures:
        raise SystemExit(1)
    subset = vc.Plan(ac.ROOT, evidence.SOURCES, policy['coordinator_checks'])
    import control_workspace
    def snapshot(root, destination):
        # These localization regressions exercise saved grading/preparation.
        # Copy verified closed-grader AC/CAS records too; do not force a cold
        # compiler/Python image extraction merely because the worker is new.
        kinds = frozenset(('capability_prepare_v1', 'capability_target_build_v1',
                           'structured_localization_v1'))
        with patch.object(control_workspace, 'ACTION_KINDS', kinds):
            return control_workspace.snapshot(root, destination)
    outcome = vc.execute(subset, jobs=args.jobs, snapshot=snapshot)
    if not outcome['passed']:
        raise SystemExit(1)
    plan.unchanged()
    current, _, _ = build(plan, cache)
    if current.key != action.key:
        raise RuntimeError('Incremental acceptance inputs changed during checks')
    value = {'kind': KIND, 'passed': True, 'historical_checks': len(names),
        'focused_checks': result.testsRun, 'coordinator_checks': len(subset.tests),
        'coordinator_cache_hits': outcome['cache_hits'], 'coordinator_executed': outcome['executed'],
        'model_api_calls': 0, 'provider_launches': 0, 'action': action.key,
        'baseline_receipt_sha256': policy['baseline_receipt_sha256']}
    cache.commit(action, {'validation.json': ac.canonical(value)})
    vc.read_file(plan.root / POLICY)  # Reject replacement/nonregular policy.
    receipt = plan.root / RECEIPT
    if receipt.exists():
        cache.put(vc.read_file(receipt))
    from parallel_controls import write_json
    write_json(receipt, value)
    print(json.dumps(require(plan, cache)), flush=True)


if __name__ == '__main__':
    main()
