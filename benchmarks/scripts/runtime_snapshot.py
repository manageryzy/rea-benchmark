"""Trust a runtime after one content capture per coordinator process/harness.

Explicit startup-once policy for this controlled benchmark host. No persistent
pass is imported, and no claim is made to detect later installation changes.
Task/configuration and provider-route verification remain outside this cache.
"""
import copy
import hashlib
import json
import os
import threading

_LOCK = threading.RLock()
_PID = os.getpid()
_RUNTIMES = {}


def populate(harness, capture):
    import startup_policy
    if startup_policy.trusted():
        import runtime_attestation
        return startup_policy.pinned_runtime(harness, runtime_attestation.ROOT, runtime_attestation.config())
    return capture(harness, fresh=True)


def reuse(value):
    # Captures are compact CAS manifests, not copies of the installed file maps.
    # Callers cannot accidentally mutate the process's admitted baseline.
    return copy.deepcopy(value)


def current(harness, *, root, policy, capture):
    global _PID
    import startup_policy
    key = (str(root), harness, startup_policy.trusted(),
           os.environ.get('REA_PINNED_IMAGE_ID') if startup_policy.trusted() else None,
           os.environ.get('REA_PINNED_IMAGE_MANIFEST') if startup_policy.trusted() else None,
           hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest())
    with _LOCK:
        # A fork starts a new admission lifetime too.
        if os.getpid() != _PID:
            _RUNTIMES.clear()
            _PID = os.getpid()
        if key not in _RUNTIMES:
            _RUNTIMES[key] = populate(harness, capture)
        return reuse(_RUNTIMES[key])


def active(harness):
    with _LOCK:
        return _PID == os.getpid() and any(k[1] == harness for k in _RUNTIMES)
