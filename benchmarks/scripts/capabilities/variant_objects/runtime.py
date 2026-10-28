"""T4 facade over the shared closed object-probe engine."""
import action_cache
from grading.c_executor import runtime as executor
from ..object_probes import Engine, sha
from . import contracts

ROOT = action_cache.ROOT
MODULE = 'benchmarks/scripts/capabilities/variant_objects/'
PREFIX = 'benchmarks/suites/capabilities-v1/sources/variants/'
POLICY = ROOT / 'benchmarks/config/variant-object-probes.json'


def require_validation():
    from . import evidence
    evidence.require_validation()


engine = Engine(module=MODULE, prefix=PREFIX, policy=POLICY, contracts=contracts,
                capability='T4', namespace='variant_object', family='variable_objects',
                validation=require_validation)
sources = engine.sources
platform = engine.platform
policy = engine.policy
compile_action = engine.compile_action
original = engine.original
validate_target = engine.validate_target
probe = engine.probe
native = engine.native
observe = engine.observe
vectors = engine.vectors
grade_action = engine.grade_action
grade = engine.grade
