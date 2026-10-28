"""Process-local reuse of read-only admission with filesystem mutation guards.

No receipts or action keys are changed. Only successful calls are retained;
hits guard files, parent identities and directory enumerations read by the
original function. Read-only preparation inspections coalesce nested guards
within a transaction and recheck fresh metadata before returning any result.
Runtime capture/verification and model dispatch are never cached. This is a
metadata optimization, not an immutable filesystem claim.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from contextvars import copy_context, ContextVar
import copy
import functools
import importlib
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import threading
from types import CodeType, FunctionType
from unittest.mock import patch

from shared_json import freeze

_CODE_ROOT = str(Path(__file__).resolve().parent) + '/'
_SESSION = ContextVar('benchmark_read_session', default=None)


@contextmanager
def startup_preparation(enabled=False):
    """Explicit startup-once trust, only during serial batch preparation.

    First admissions still execute the real validators. Subsequent uses trust
    their inputs until this scope ends. This is not a mutation-detection claim;
    strict callers outside the scope retain their normal fresh guards.
    """
    if not enabled:
        yield
        return
    session = _SESSION.get()
    if session is None:
        with ReadSession() as session, session.startup_scope():
            yield
    else:
        with session.startup_scope():
            yield


@contextmanager
def mutable_preparation():
    """Builders may create inputs; their next admission must be fresh."""
    session = _SESSION.get()
    if session is None or session.startup_epoch is None:
        yield
        return
    session.startup_epoch = None
    session.clear_admission()
    try:
        yield
    finally:
        session.clear_admission()
        session.startup_epoch = ReadEpoch()

TARGETS = {
    'capabilities.evidence': ('require_validation',),
    'capabilities.analysis_context': ('require_validation', 'gate_fingerprints', 'public_catalog'),
    'capabilities.tasks': ('declared', 'frozen'),
    'capabilities.config': ('load',),
    'capabilities.build': ('tooling',),
    'capabilities.preparation': ('inspect', 'action'),
    'capabilities.store': ('spec', 'fixed_inputs'),
    'capabilities.registration': ('scoring_state', 'require_validation'),
    'cache': ('action_input_bytes', 'profile_bytes'),
    'environment_profile': ('current',),
    'grading.image_manifest': ('load',),
    'capabilities.pointers.tasks': ('validate_original_binding',),
    'capabilities.native_abi.tasks': ('validate_original',),
    'capabilities.optimized_locations.tasks': ('validate_original',),
    'capabilities.baselines.tasks': ('validate_private',),
    'capabilities.baselines.task_runtime': ('require_validation',),
}


# Only explicitly audited read-only APIs share returned object identity. Other
# cached results retain defensive copies (callers may edit declarations/actions).
SHARED_JSON = frozenset({('grading.image_manifest', 'load')})
# These calls are read-only inspections, not builders or inference admission.
# A single inspection gets one observed metadata snapshot and a fresh exit check.
TRANSACTIONAL = frozenset({('capabilities.preparation', 'inspect')})


class ReadEpoch:
    def __init__(self):
        self.signatures = {}
        self.cas = {}
        self.lock = threading.RLock()

    def observe(self, path):
        name = str(Path(path).absolute())
        with self.lock:
            if name not in self.signatures:
                self.signatures[name] = signature(name)
            return self.signatures[name]


def clone_cache_value(value):
    return copy.deepcopy(value)


def freeze_cache_value(value):
    return freeze(value)


def return_cache_value(value, shared):
    return value if shared else clone_cache_value(value)


def profile_arguments(args, kwargs):
    """profile_bytes consumes only environment and condition, not the task ID.

    Its file/registry/archive dependencies are still captured by Reads. A new
    model task cannot force re-reading identical historical adapter bytes.
    """
    identity = args[0] if args else kwargs['identity']
    return [identity['environment'], identity['condition']]


def verify_inspection(reads):
    """Fresh boundary check, deliberately outside the epoch's metadata cache."""
    return reads.current()


def stable_callable(value):
    """Known metadata scopes wrap identical validators with fresh closures.

    Compare their captured implementation, not the temporary wrapper identity.
    Arbitrary replacements/mocks remain different and still invalidate admission.
    """
    if (not isinstance(value, FunctionType)
            or not value.__qualname__.startswith('metadata_validation_cache.<locals>.')):
        return value
    module = sys.modules.get('grading.metadata_admission')
    factory = getattr(module, 'metadata_validation_cache', None)
    factory = getattr(factory, '__wrapped__', factory)
    code = getattr(factory, '__code__', None)
    if code is None:
        return value
    wrappers = {c for c in code.co_consts if isinstance(c, CodeType) and c.co_name in ('load', 'validate')}
    while isinstance(value, FunctionType) and value.__code__ in wrappers:
        key = 'original_load' if value.__code__.co_name == 'load' else 'original_validate'
        closed = dict(zip(value.__code__.co_freevars, value.__closure__ or ()))
        if key not in closed:
            break
        value = closed[key].cell_contents
    return value


def semantic_context():
    """Exclude only the known manifest loader's per-scope bookkeeping token.

    A fresh metadata_validation_cache creates a fresh ContextVar on each entry.
    Its identity does not change policy or bytes; including it in admission keys
    defeats cross-case reuse. All caller/registration/offline contexts remain.
    """
    # These contexts hold caches, not task semantics; caches are cleared at
    # their boundaries. Including mutable cache contents defeats memoization.
    ignored = {_SESSION}
    batch_module = sys.modules.get('preparation_session')
    if batch_module is not None:
        ignored.add(batch_module._BATCH)
    module = sys.modules.get('grading.metadata_admission')
    factory = getattr(module, 'metadata_validation_cache', None)
    factory = getattr(factory, '__wrapped__', factory)
    code = getattr(factory, '__code__', None)
    known = {c for c in code.co_consts if isinstance(c, CodeType) and c.co_name == 'load'} if code else set()
    loader = getattr(sys.modules.get('grading.image_manifest'), 'load', None)
    while isinstance(loader, FunctionType):
        if loader.__code__ in known:
            closed = dict(zip(loader.__code__.co_freevars, loader.__closure__ or ()))
            if 'inside_load' in closed:
                ignored.add(closed['inside_load'].cell_contents)
            loader = closed.get('original_load')
            loader = loader.cell_contents if loader is not None else None
        else:
            loader = getattr(loader, '__wrapped__', None)
    return sorted((id(var), value) for var, value in copy_context().items() if var not in ignored)


def loaded_code():
    """Changed/mocked loaded helpers must not inherit an earlier admission."""
    result = []
    for name, module in list(sys.modules.items()):
        if not str(getattr(module, '__file__', '')).startswith(_CODE_ROOT):
            continue
        for key, value in list(vars(module).items()):
            if callable(value):
                value = stable_callable(value)
                result.append((name, key, id(value), id(getattr(value, '__code__', None))))
            if isinstance(value, type) and value.__module__ == name:
                result.extend((name + '.' + key, member, id(method), id(getattr(method, '__code__', None)))
                    for member, method in list(vars(value).items()) if callable(method))
    return tuple(result)


def code_current(expected):
    current = {(module, name): (identity, code) for module, name, identity, code in loaded_code()}
    return all(current.get((module, name)) == (identity, code)
               for module, name, identity, code in expected)


def signature(path):
    try:
        s = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def buffer_read(stream):
    """Separate actual IO from metadata guards for timing telemetry."""
    return stream.read()


class Reads:
    def __init__(self, originals, executor=None):
        self.originals = originals
        self.executor = executor
        self.files, self.directories, self.queries = {}, {}, {}
        self.lock = threading.RLock()
        self.changed = False

    def remember(self, path, before, after, observe=None):
        path = str(Path(path).absolute())
        with self.lock:
            old = self.files.setdefault(path, before)
            self.changed |= old != before or before != after
            # Identity guards catch directory/symlink replacement. Membership
            # is checked separately for every actual enumeration below.
            for parent in Path(path).parents:
                if str(parent) in self.directories:
                    continue
                value = (observe or signature)(parent)
                identity = value[:3] if value else None
                prior = self.directories.setdefault(str(parent), identity)
                self.changed |= prior != identity

    def query(self, path, method, args, values):
        key = (str(Path(path).absolute()), method, tuple(args))
        value = tuple(sorted(str(p) for p in values))
        with self.lock:
            old = self.queries.setdefault(key, value)
            self.changed |= old != value

    def merge(self, other):
        """A cached child still contributes every dependency to its parent."""
        with self.lock:
            self.changed |= other.changed
            for destination, source in ((self.files, other.files),
                                        (self.directories, other.directories),
                                        (self.queries, other.queries)):
                for key, value in source.items():
                    self.changed |= destination.setdefault(key, value) != value

    def current(self, observe=None):
        if self.changed:
            return False
        def check(item):
            path, expected, identity = item
            try:
                value = (observe or signature)(path)
                return (value[:3] if identity and value else value) == expected
            except OSError:
                return False
        checks = [(p, s, False) for p, s in self.files.items()]
        checks += [(p, s, True) for p, s in self.directories.items()]
        if self.executor is not None:
            if not all(self.executor.map(check, checks)):
                return False
        else:
            if not all(map(check, checks)):
                return False
        for (path, method, args), expected in self.queries.items():
            try:
                observed = tuple(sorted(str(p) for p in self.originals[method](Path(path), *args)))
            except OSError:
                return False
            if observed != expected:
                return False
        return True


class ReadSession:
    def __init__(self, targets=None, limit=2048):
        self.targets = TARGETS if targets is None else targets
        self.limit = limit
        self.cache = OrderedDict()
        self.pending = {}
        self.byte_cache = OrderedDict()
        self.byte_cache_bytes = 0
        self.byte_lock = threading.RLock()
        self.lock = threading.RLock()
        self.active = None
        self.epoch = None
        self.startup_epoch = None
        self.hits = self.misses = self.invalidations = 0
        self.originals = {name: getattr(Path, name) for name in
            ('open', 'read_bytes', 'exists', 'is_file', 'is_dir', 'is_symlink', 'iterdir', 'glob', 'rglob')}
        self.executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix='admission-stat')

    @contextmanager
    def startup_scope(self):
        # Per-cell preparation can overlap another model's grading. Serialize
        # epoch changes with metadata admission, never clear its in-flight map.
        with self.lock:
            if self.startup_epoch is not None:
                yield
                return
            # Do not turn a pass from an earlier scope into current admission.
            self.clear_admission()
            self.startup_epoch = ReadEpoch()
            try:
                yield
            finally:
                self.startup_epoch = None
                self.clear_admission()

    def clear_admission(self):
        self.cache.clear()
        self.pending.clear()
        self.byte_cache.clear()
        self.byte_cache_bytes = 0

    def record_path(self, path):
        active = self.active
        if active is not None:
            epoch = self.epoch or self.startup_epoch
            observe = epoch.observe if epoch is not None else signature
            value = observe(path)
            active.remember(path, value, value, observe)
            linked = value is not None and stat.S_ISLNK(value[2])
            linked |= any(identity is not None and stat.S_ISLNK(identity[2])
                for p in Path(path).absolute().parents
                if (identity := active.directories.get(str(p))) is not None)
            if linked:
                target = Path(path).resolve()
                value = observe(target)
                active.remember(target, value, value, observe)
                path = target
            return str(Path(path).absolute()), value
        return None

    def read_bytes(self, path):
        observed = self.record_path(path)
        if observed is None:
            return self.originals['read_bytes'](path)
        name, before = observed
        with self.byte_lock:
            previous = self.byte_cache.get(name)
            if previous is not None and previous[0] == before:
                self.byte_cache.move_to_end(name)
                return previous[1]
        with self.originals['open'](path, 'rb') as stream:
            raw = buffer_read(stream)
        epoch = self.epoch or self.startup_epoch
        after = epoch.observe(name) if epoch is not None else signature(name)
        self.active.remember(name, before, after, epoch.observe if epoch else None)
        if before == after and before is not None and stat.S_ISREG(before[2]) and len(raw) <= 16 * 1024 * 1024:
            with self.byte_lock:
                old = self.byte_cache.pop(name, None)
                if old is not None:
                    self.byte_cache_bytes -= len(old[1])
                self.byte_cache[name] = (after, raw)
                self.byte_cache_bytes += len(raw)
                while self.byte_cache_bytes > 64 * 1024 * 1024:
                    _, (_, discarded) = self.byte_cache.popitem(last=False)
                    self.byte_cache_bytes -= len(discarded)
        return raw

    def memoize(self, function, *, shared=False, transactional=False, arguments=None):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            # Nested admissions reuse the same guarded entries as direct calls.
            # Merge child read sets even on hits, so the parent's later hit cannot
            # hide a changed child input. Serialize ownership for helper threads.
            with self.lock:
                parent = self.active
                try:
                    context = semantic_context()
                    key = (function, shared, transactional, os.getcwd(),
                        hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True).encode()).digest(),
                        json.dumps([arguments(args, kwargs) if arguments else [args, kwargs], context], sort_keys=True,
                        default=lambda v: sorted(v) if isinstance(v, (set, frozenset)) else
                            (_ for _ in ()).throw(TypeError('Unsupported memo argument'))))
                except (TypeError, ValueError):
                    return function(*args, **kwargs)
                available = self.cache if key in self.cache else self.pending
                if key in available:
                    reads, value, code = available[key]
                    valid = self.startup_epoch is not None or (code_current(code) and
                        (reads.current(self.epoch.observe) if self.epoch else reads.current()))
                    if valid:
                        if parent is not None:
                            parent.merge(reads)
                        self.hits += 1
                        if available is self.cache:
                            self.cache.move_to_end(key)
                        return return_cache_value(value, shared)
                    self.invalidations += 1
                    del available[key]
                self.misses += 1
                reads = Reads(self.originals, self.executor)
                if parent is None:
                    self.pending = {}
                owns_epoch = transactional and parent is None and self.epoch is None and self.startup_epoch is None
                if owns_epoch:
                    self.epoch = ReadEpoch()
                self.active = reads
                try:
                    value = function(*args, **kwargs)
                    if owns_epoch and not verify_inspection(reads):
                        raise RuntimeError('Preparation inputs changed during inspection; result discarded')
                except BaseException:
                    if parent is None:
                        self.pending.clear()
                    raise
                finally:
                    self.active = parent
                    if owns_epoch:
                        self.epoch = None
                    if parent is not None:
                        parent.merge(reads)
                saved_value = freeze_cache_value(value) if shared else clone_cache_value(value)
                self.pending[key] = (reads, saved_value, loaded_code())
                if parent is None:
                    try:
                        # Every nested read set is already merged into the root.
                        # Validate that union once before publishing child hits;
                        # never rescan the same files at each nesting level.
                        if self.startup_epoch is not None or owns_epoch or reads.current():
                            current = {(m, n): (i, c) for m, n, i, c in loaded_code()}
                            for candidate, (dependencies, saved, code) in self.pending.items():
                                if not dependencies.changed and all(current.get((m, n)) == (i, c) for m, n, i, c in code):
                                    self.cache[candidate] = (dependencies, saved, code)
                            while len(self.cache) > self.limit:
                                self.cache.popitem(last=False)
                    finally:
                        self.pending.clear()
                return saved_value if shared else value
        return wrapped

    def cas_get(self, original, cache, value):
        """Reuse verified bytes only within one guarded inspection transaction."""
        epoch = self.epoch or self.startup_epoch
        if epoch is None or self.active is None:
            return original(cache, value)
        key = (str(Path(cache.root).absolute()), json.dumps(value, sort_keys=True))
        if key in epoch.cas:
            raw, reads = epoch.cas[key]
            self.active.merge(reads)
            return raw
        parent = self.active
        reads = Reads(self.originals, self.executor)
        self.active = reads
        try:
            raw = original(cache, value)
        finally:
            self.active = parent
            parent.merge(reads)
        epoch.cas[key] = (raw, reads)
        return raw

    def __enter__(self):
        self.session_token = _SESSION.set(self)
        self.patches = ExitStack()
        original = self.originals['open']
        def opened(path, mode='r', *args, **kwargs):
            if self.epoch is not None and any(c in mode for c in 'wax+'):
                self.active.changed = True
                raise RuntimeError('Writing inside read-only preparation inspection is forbidden')
            self.record_path(path)
            if self.active is not None and any(c in mode for c in 'wax+'):
                self.active.changed = True
            result = original(path, mode, *args, **kwargs)
            return result
        self.patches.enter_context(patch.object(Path, 'open', opened))
        def read_bytes(path): return self.read_bytes(path)
        read_bytes.__rea_bytecache__ = True
        self.patches.enter_context(patch.object(Path, 'read_bytes', read_bytes))
        for method in ('exists', 'is_file', 'is_dir', 'is_symlink'):
            def predicate(path, *args, _method=method, **kwargs):
                self.record_path(path)
                return self.originals[_method](path, *args, **kwargs)
            self.patches.enter_context(patch.object(Path, method, predicate))
        for method in ('iterdir', 'glob', 'rglob'):
            def query(path, *args, _method=method, **kwargs):
                active = self.active
                result = self.originals[_method](path, *args, **kwargs)
                if active is None:
                    return result
                values = list(result)
                if kwargs:
                    active.changed = True  # Unrepresented query options: no hit.
                active.query(path, _method, args, values)
                return iter(values)
            self.patches.enter_context(patch.object(Path, method, query))
        for module_name, names in self.targets.items():
            module = importlib.import_module(module_name)
            for name in names:
                self.patches.enter_context(patch.object(module, name, self.memoize(getattr(module, name),
                    shared=(module_name, name) in SHARED_JSON, transactional=(module_name, name) in TRANSACTIONAL,
                    arguments=profile_arguments if (module_name, name) == ('cache', 'profile_bytes') else None)))
        import action_cache
        original_get = action_cache.Cache.get
        self.patches.enter_context(patch.object(action_cache.Cache, 'get',
            lambda cache, value: self.cas_get(original_get, cache, value)))
        # Pure AST transformation: key includes every input byte and selection.
        import validation_cache
        original_project = validation_cache.project
        @functools.lru_cache(maxsize=1024)
        def project(raw, selected):
            return original_project(raw, selected)
        self.patches.enter_context(patch.object(validation_cache, 'project',
            lambda raw, selected: project(raw, frozenset(selected))))
        return self

    def __exit__(self, *error):
        self.executor.shutdown(wait=True)
        self.patches.close()
        self.cache.clear()
        self.pending.clear()
        self.byte_cache.clear()
        self.byte_cache_bytes = 0
        _SESSION.reset(self.session_token)
