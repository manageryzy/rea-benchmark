"""Source-bound preparation evidence; Rust/Go candidate runtimes remain pending."""
import hashlib
import json

import action_cache
from grading import image_manifest
from grading.c_executor import runtime as executor
from . import build, reference, tooling

ROOT = action_cache.ROOT
RECEIPT = ROOT / 'benchmarks/config/matched-baseline-validation.json'
MINIMUM_CHECKS = 16


def fingerprints():
    names = set(build.SOURCES) | set(reference.SOURCES) | {
        build.MODULE + name for name in ('__init__.py', 'build.py', 'tooling.py', 'reference.py', 'evidence.py')}
    names.update(('benchmarks/scripts/check_matched_baselines.py', 'benchmarks/scripts/action_cache.py',
        'benchmarks/scripts/capabilities/build.py', 'benchmarks/scripts/capabilities/buffers.py',
        'benchmarks/scripts/capabilities/elf.py', 'benchmarks/scripts/capabilities/native_abi/images.py',
        'benchmarks/scripts/startup_policy.py', 'benchmarks/scripts/grading/runtime.py', 'benchmarks/scripts/grading/image_manifest.py',
        'benchmarks/scripts/grading/toolchains.py',
        'benchmarks/suites/capabilities-v1/controls/buffer_solutions.py',
        'benchmarks/suites/capabilities-v1/controls/cpp_buffer_solutions.py'))
    for compiler in build.COMPILERS:
        for family in build.FAMILIES:
            names.update(str(path.relative_to(ROOT)) for path in build.inputs(compiler, family).values())
    for stage in ('c_compile', 'c_execute'): names.update(executor.sources(stage))
    from capabilities.cpp_buffer import runtime as cpp
    names.update(cpp.sources())
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def pins():
    return {'compilers': {lang: image_manifest.load(lang)['id'] for lang in ('cpp', 'rust', 'go')},
            'clang_extension': tooling.record()['id'], 'binutils': build.family_build.tooling()[0],
            'execution': executor.platform(True),
            'originals': {compiler + '/' + family: build.action(compiler, family)[0].key
                          for compiler in build.COMPILERS for family in build.FAMILIES}}


def begin():
    executor.admit()
    value = {'schema_version': 1, 'scope': 'matched_original_baselines_and_c_cpp_controls_only',
             'passed': False, 'checks': 0, 'failed': 0, 'model_api_calls': 0, 'release_accepted': False,
             'native_candidate_languages_pending': ['rust', 'go'],
             'inputs_sha256': fingerprints(), 'pins': pins()}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def finish(started, result):
    if started['inputs_sha256'] != fingerprints() or started['pins'] != pins():
        raise RuntimeError('Matched baseline sources/toolchains changed during controls')
    value = {**started, 'passed': result.wasSuccessful() and result.testsRun >= MINIMUM_CHECKS,
             'checks': result.testsRun, 'failed': len(result.failures) + len(result.errors)}
    RECEIPT.write_bytes(action_cache.canonical(value))
    return value


def current():
    value = json.loads(RECEIPT.read_bytes()) if RECEIPT.is_file() else {}
    return (type(value.get('schema_version')) is int and value['schema_version'] == 1 and value.get('passed') is True
            and type(value.get('checks')) is int and value['checks'] >= MINIMUM_CHECKS
            and value.get('failed') == value.get('model_api_calls') == 0
            and value.get('scope') == 'matched_original_baselines_and_c_cpp_controls_only'
            and value.get('release_accepted') is False and value.get('native_candidate_languages_pending') == ['rust', 'go']
            and value.get('inputs_sha256') == fingerprints() and value.get('pins') == pins())
