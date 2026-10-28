#!/usr/bin/python3
"""Bounded, receipt-backed verification of already prepared benchmark caches.

This is an integrity/reuse check, not a replacement for cold semantic acceptance
or fresh runtime/provider attestation. Never builds, grades, or calls a model.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time
from unittest.mock import patch

import action_cache

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/warm-cache-validation.json'
# Startup text is not part of the semantic cache-validation proof. Changes to
# effective models, cases, adapters, policies and validator sources still fail.
STARTUP_ONLY = {'Makefile', 'bin/benchmark'}


def forbidden(*args, **kwargs):
    raise RuntimeError('Warm-cache verification cannot start processes or access providers; use make check-cold')


class Snapshot:
    """One content read per path, followed by a fresh content seal before success."""
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.raw = {}

    def read(self, name):
        name = str(action_cache.relative(name))
        path = self.root / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('Warm input escapes workspace: ' + name)
        if name not in self.raw:
            self.raw[name] = path.read_bytes()
        return self.raw[name]

    def json(self, name):
        return json.loads(self.read(name))

    def verify(self, expected):
        def check(item):
            name, wanted = item
            if hashlib.sha256(self.read(name)).hexdigest() != wanted:
                raise ValueError('Warm input changed; cold acceptance/preparation required: ' + name)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(check, expected.items()))

    def seal(self):
        expected = {name: hashlib.sha256(raw).hexdigest() for name, raw in self.raw.items()}
        def check(item):
            name, wanted = item
            path = self.root / name
            if (path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve())
                    or hashlib.sha256(path.read_bytes()).hexdigest() != wanted):
                raise ValueError('Warm input changed during verification: ' + name)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(check, expected.items()))
        return expected


class Timings:
    def __init__(self, budget):
        self.started = time.monotonic()
        self.budget = budget
        self.rows = []

    @contextmanager
    def stage(self, name):
        if time.monotonic() - self.started > self.budget:
            raise RuntimeError('Warm-cache time budget exceeded')
        print('WARM STAGE: ' + name, flush=True)
        started = time.monotonic()
        try:
            yield
        finally:
            seconds = time.monotonic() - started
            self.rows.append({'name': name, 'seconds': round(seconds, 6)})
            print(f'WARM TIMING: {name}: {seconds:.3f}s', flush=True)


def verify_receipt(snapshot, name):
    value = snapshot.json('benchmarks/config/' + name)
    if (value.get('schema_version') != 1 or value.get('passed') is not True
            or value.get('model_api_calls') != 0 or value.get('provider_launches') != 0
            or not value.get('inputs_sha256')):
        raise ValueError('Missing successful offline acceptance: ' + name)
    snapshot.verify({key: sha for key, sha in value['inputs_sha256'].items() if key not in STARTUP_ONLY})
    return value


def verify_packages(snapshot, pins):
    from capabilities import tasks, preparation
    from grading import image_manifest
    declared = {row['task_id']: row for row in tasks.declared()}
    pinned = {row['task_id']: row for row in pins['tasks']}
    if len(pinned) != len(pins['tasks']) or set(declared) != set(pinned):
        raise ValueError('Prepared case inventory changed; run make prepare-capabilities')
    load = image_manifest.load
    admitted = {}
    def admitted_image(language, *, cache=image_manifest.CACHE):
        key = (language, str(cache))
        if key not in admitted:
            record = load(language, cache=cache)
            active = snapshot.json(str((Path(cache) / 'active.json').relative_to(snapshot.root)))
            profile = snapshot.json(str((Path(cache) / 'profiles' / (record['id'] + '.json')).relative_to(snapshot.root)))
            snapshot.read(str(image_manifest.CONFIG.relative_to(snapshot.root)))
            if active[language]['id'] != record['id'] or profile != record:
                raise ValueError('Grading image changed during admission')
            admitted[key] = record
        return admitted[key]
    with patch.object(image_manifest, 'load', side_effect=admitted_image):
        for name, pin in pinned.items():
            value = snapshot.json('.cache/benchmark-capabilities/task-index/' + name + '.json')
            if (value['id'] != name or value['task_id'] != name
                    or any(value.get(key) != expected for key, expected in pin.items() if key != 'capability_ids')
                    or value.get('capability_ids') != pin.get('capability_ids')):
                raise ValueError('Prepared case differs from its accepted pin: ' + name)
            package = '.cache/benchmark-capabilities/task-freezes/' + value['public_package_sha256']
            spec = '.cache/benchmark-capabilities/grade-specs/' + value['grading_spec_sha256'] + '.json'
            if value['public_directory'] != package or value['grading_spec'] != spec:
                raise ValueError('Prepared case escapes immutable storage: ' + name)
            if set(value['public_files_sha256']) != {'target', 'prompt.txt', 'tools.json', 'manifest.json'}:
                raise ValueError('Prepared public input set changed: ' + name)
            if action_cache.digest(action_cache.canonical(value['public_files_sha256']))['hash'] != value['public_package_sha256']:
                raise ValueError('Prepared package Merkle identity changed: ' + name)
            snapshot.verify({package + '/' + key: sha for key, sha in value['public_files_sha256'].items()} | {spec: value['grading_spec_sha256']})
            if hashlib.sha256(snapshot.read(package + '/target')).hexdigest() != pin['binary_sha256']:
                raise ValueError('Prepared target changed: ' + name)
            # Recompute the declared build action, but never execute it. Its verified
            # CAS record binds the complete task index/private spec, not just hashes
            # copied from that same index. Toolchain admission is shared per image.
            selected = preparation.action(declared[name])
            local = action_cache.Cache()
            hit = local.lookup(selected)
            if hit is None:
                raise ValueError('Prepared action missing or stale; run make prepare-capabilities: ' + name)
            expected, files = preparation._read(local, hit, declared[name])
            if expected != value or files['private/grading.json'] != snapshot.read(spec):
                raise ValueError('Prepared selection differs from its action cache: ' + name)
            snapshot.read(str(local._path('ac', selected.key).relative_to(snapshot.root)))
            for info in hit['output_files'].values():
                snapshot.read(str(local._path('cas', info['digest']['hash']).relative_to(snapshot.root)))
    return len(pinned)


def verify_native_artifacts(snapshot, receipt):
    completed = [name for name in receipt['inputs_sha256'] if name.endswith('/completed.json')]
    for name in completed:
        directory = name.rsplit('/', 1)[0]
        complete = snapshot.json(name)
        snapshot.verify({directory + '/' + str(action_cache.relative(key)): sha
                         for key, sha in complete['files_sha256'].items()})
    if len(completed) != receipt['historical_completed_tasks']:
        raise ValueError('Saved native acceptance denominator changed')
    return len(completed)


def run(*, budget=180, root=ROOT, cache=None):
    snapshot = Snapshot(root)
    snapshot.read('benchmarks/scripts/check_warm_cache.py')
    snapshot.read('benchmarks/scripts/action_cache.py')
    timings = Timings(budget)
    with ExitStack() as guards:
        for obj, name in ((socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                          (socket, 'create_connection'), (socket, 'getaddrinfo'), (subprocess, 'Popen')):
            guards.enter_context(patch.object(obj, name, forbidden))
        with timings.stage('acceptance_receipts'):
            runner = verify_receipt(snapshot, 'runner-validation.json')
            coordinator = verify_receipt(snapshot, 'capability-coordinator-validation.json')
            pins = snapshot.json('benchmarks/config/capability-task-pins.json')
            if pins.get('schema_version') != 1 or pins.get('model_api_calls') != 0 or not pins.get('inputs_sha256'):
                raise ValueError('Missing frozen preparation acceptance')
            snapshot.verify(pins['inputs_sha256'])
        with timings.stage('prepared_case_bytes'):
            cases = verify_packages(snapshot, pins)
        with timings.stage('saved_native_artifacts'):
            native = verify_native_artifacts(snapshot, runner)
        # The closure consists of exact bytes consulted above. No timestamps,
        # report scripts, temporary paths or scheduling text enter this key.
        snapshot.read('benchmarks/scripts/check_warm_cache.py')
        snapshot.read('benchmarks/scripts/action_cache.py')
        with timings.stage('fresh_content_seal'):
            inputs = snapshot.seal()
        with timings.stage('persistent_action_cache'):
            action = action_cache.Action('warm_cache_verification_v1',
                {'verified-inputs.json': action_cache.canonical(inputs)},
                arguments=['verify-prepared-bytes-and-accepted-receipts'])
            local = cache or action_cache.Cache()
            hit = local.lookup(action)
            cache_hit = hit is not None
            proof = {'prepared_cases': cases, 'native_saved_cases': native,
                     'reusable_native_cases': runner['configured_tasks'],
                     'retained_changed_input_cases': len(runner['changed_input_cache_misses']),
                     'accepted_regression_checks': coordinator['checks'],
                     'provider_launches': 0, 'model_api_calls': 0}
            if not hit:
                hit = local.commit(action, {'verification.json': action_cache.canonical(proof)})
            observed = json.loads(local.get(hit['output_files']['verification.json']['digest']))
            if observed != proof:
                raise ValueError('Persistent warm verification cache differs')
    total = time.monotonic() - timings.started
    if total > budget:
        raise RuntimeError(f'Warm-cache verification took {total:.3f}s; budget is {budget}s')
    value = {'schema_version': 1, 'scope': 'warm_artifact_integrity', 'passed': True,
             'measured_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'total_seconds': round(total, 6), 'budget_seconds': budget, 'stages': timings.rows,
             **proof, 'verification_action': action.key, 'verification_cache_hit': cache_hit,
             'inputs_sha256': inputs}
    path = Path(root) / 'benchmarks/config/warm-cache-validation.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    import runtime_storage
    runtime_storage.atomic(path, action_cache.canonical(value) + b'\n')
    print(f'WARM PASS: {cases} prepared cases; {native} saved native cases; {total:.3f}s; zero model calls', flush=True)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-seconds', type=int, default=180)
    args = parser.parse_args()
    if args.budget_seconds <= 0:
        parser.error('budget must be positive')
    run(budget=args.budget_seconds)


if __name__ == '__main__':
    main()
