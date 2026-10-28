"""Bounded metadata-admission reuse; file loading and payload checks stay active."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import sys
from types import BuiltinFunctionType, FunctionType
from unittest.mock import patch


@contextmanager
def metadata_validation_cache(module=None):
    """Memoize pure admission of identical parsed manifests within one preparation session.

    Every load still reads its selected files, verifies their content identity and
    checks current policy. Payload/CAS checks are untouched. Direct validation and
    mocked admission dependencies always use the original validator.
    """
    if module is None:
        from grading import image_manifest as module
    original_load, original_validate = module.load, module.validate_record
    trusted_functions = (original_load, original_validate, module.relative, module.policy,
                         module.canonical, module.re.fullmatch, module.platform.machine,
                         module.json.loads, module.json.dumps, hashlib.sha256, sys._getframe)
    if (not isinstance(original_load, FunctionType) or not isinstance(original_validate, FunctionType)
            or any(not isinstance(function, (FunctionType, BuiltinFunctionType))
            for function in trusted_functions) or not isinstance(module.PurePosixPath, type)):
        # A mock installed before this scope must retain its direct-call behavior.
        yield
        return
    inside_load = ContextVar('inside_manifest_load', default=False)
    admitted = {}

    def dependencies():
        functions = [getattr(module, name) for name in ('relative', 'policy', 'canonical')]
        return (tuple((function, getattr(function, '__code__', None)) for function in functions),
                module.PurePosixPath, module.PurePosixPath.parents,
                module.re.fullmatch, module.platform.machine, module.sys.platform,
                module.json.loads, module.json.dumps, hashlib.sha256, sys._getframe,
                original_load.__code__, original_validate.__code__)

    expected_dependencies = dependencies()

    def validate(record, language):
        unchanged = dependencies() == expected_dependencies
        # `from image_manifest import load` retains the original function even
        # while module.load is wrapped. Its immediate code frame proves the
        # same fresh-reading loader path; direct validate_record stays uncached.
        from_loader = inside_load.get() or (unchanged and sys._getframe(1).f_code is original_load.__code__)
        if not from_loader or not unchanged:
            return original_validate(record, language)
        policy = module.policy(language)
        key = hashlib.sha256(module.canonical([language, record, policy])).digest()
        if key in admitted:
            return None
        result = original_validate(record, language)
        if dependencies() == expected_dependencies and module.policy(language) == policy:
            if len(admitted) >= 32:
                admitted.pop(next(iter(admitted)))
            admitted[key] = True
        return result

    def load(*args, **kwargs):
        token = inside_load.set(True)
        try:
            return original_load(*args, **kwargs)
        finally:
            inside_load.reset(token)

    with patch.object(module, 'load', load), patch.object(module, 'validate_record', validate):
        yield

