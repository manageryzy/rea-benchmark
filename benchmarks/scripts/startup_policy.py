"""Explicit local execution policy; offline acceptance remains a separate command.

trusted_local trusts the selected read-only tool image and prepares one cell at
a time. It never turns historical test receipts into current passing evidence.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_MODE = ContextVar('benchmark_startup_mode', default='strict')


def trusted():
    return _MODE.get() == 'trusted_local'


@contextmanager
def scope(mode):
    if mode not in ('strict', 'trusted_local'):
        raise ValueError('startup_mode must be strict or trusted_local')
    token = _MODE.set(mode)
    try:
        yield
    finally:
        _MODE.reset(token)


def offline_gate(function):
    """Skip test-receipt admission only in the explicitly selected local mode."""
    from functools import wraps
    @wraps(function)
    def checked(*args, **kwargs):
        if trusted():
            return None
        return function(*args, **kwargs)
    return checked


def execution(function):
    from functools import wraps
    @wraps(function)
    def configured(selected, selection, *args, **kwargs):
        with scope(selection['execution'].get('startup_mode', 'strict')):
            return function(selected, selection, *args, **kwargs)
    return configured


def pinned_runtime(harness, root, policy):
    import hashlib
    import json
    import os
    import platform
    from pathlib import Path
    import action_cache
    import runtime_attestation
    image = os.environ.get('REA_PINNED_IMAGE_ID', '')
    manifest_id = os.environ.get('REA_PINNED_IMAGE_MANIFEST', '')
    for value in (image, manifest_id):
        action_cache.valid_digest({'hash': value, 'size_bytes': 0})
    # The launcher has already verified the image and mounted its contents
    # read-only. Only mutable benchmark adapters need new content hashes.
    names = set(runtime_attestation.CONTROL) | {'benchmarks/scripts/startup_policy.py'}
    for spec in policy['components'].values():
        if harness in spec['harnesses']:
            names.update(p for p in spec['paths'] if p.startswith(('benchmarks/', 'bin/')) or p == 'env.sh')
    files = {name: hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() for name in sorted(names)}
    manifest = {'schema_version': 1, 'harness': harness,
        'policy': 'trusted-local-pinned-image-v1',
        'scope': 'launcher-verified-read-only-image; mutable-adapter-digests; offline-acceptance-not-rechecked',
        'components': {}, 'pinned_image_sha256': image, 'pinned_manifest_sha256': manifest_id,
        'mutable_files_sha256': files,
        'environment': policy['runtime_environment'], 'network': policy['network'],
        'platform': {'system': platform.system(), 'machine': platform.machine(), 'kernel_release': platform.release()}}
    return {'identity': hashlib.sha256(action_cache.canonical(manifest)).hexdigest(), 'manifest': manifest}
