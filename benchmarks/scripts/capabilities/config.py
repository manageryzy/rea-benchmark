"""Strict configuration for the permanent capability suite; planning is read-only."""
import json
from pathlib import Path

import action_cache
import environment_profile
from grading.artifacts import identifier, integer, object_fields

ROOT = action_cache.ROOT
DEFAULT = ROOT / 'benchmarks/config/capabilities.json'


def paging_policy(settings):
    condition = settings['condition']
    if condition not in ('paged-evidence-v2', 'compact-evidence-v3', 'lean-evidence-v4'):
        raise ValueError('Invalid capability evidence condition')
    path = environment_profile.paging_policy_path(condition)
    if path is None:
        raise ValueError('Capability evidence condition has no declared paging policy')
    value = json.loads(path.read_bytes())
    if value.get('id') != condition:
        raise ValueError('Capability paging policy differs from its condition')
    return value


def load(path=DEFAULT):
    value = json.loads(Path(path).read_bytes())
    object_fields(value, ('schema_version', 'suite', 'inference_enabled', 'families_file', 'pricing_file',
                         'analysis_tools_file', 'condition', 'limits', 'runtime', 'profiles'))
    integer(value['schema_version'], 1, 1)
    if value['suite'] != 'capabilities-v1' or type(value['inference_enabled']) is not bool:
        raise ValueError('Invalid capability-suite selection')
    paging_policy(value)
    for name in ('families_file', 'pricing_file', 'analysis_tools_file'):
        action_cache.relative(value[name])
    object_fields(value['limits'], ('max_tool_calls', 'timeout_seconds', 'max_output_tokens'))
    for number in value['limits'].values():
        integer(number, 1, 1000000)
    object_fields(value['runtime'], ('schema_version', 'harness_version', 'tool_timeout_ms', 'data_limits'), ('known_entry_seeds', 'explicit_type_roles'))
    from .analysis_context import enabled
    enabled(value['runtime'])
    from .artifact_format import enabled as format_enabled
    format_enabled(value['runtime'])
    integer(value['runtime']['schema_version'], 4, 4)
    integer(value['runtime']['tool_timeout_ms'], 1, 300000)
    if value['runtime']['harness_version'] != '0.2.1-rc.2' or not isinstance(value['runtime']['data_limits'], dict):
        raise ValueError('Unsupported capability runtime configuration')
    if not isinstance(value['profiles'], list) or not value['profiles']:
        raise ValueError('Capability profiles must be configured')
    seen = set()
    for profile in value['profiles']:
        object_fields(profile, ('id', 'kind', 'enabled', 'output_language', 'artifact_schema'), ('families', 'conventions', 'condition', 'variant', 'original_compiler'))
        identifier(profile['id'])
        if profile['id'] in seen or type(profile['enabled']) is not bool:
            raise ValueError('Duplicate or invalid capability profile')
        seen.add(profile['id'])
        if 'original_compiler' in profile and profile['kind'] != 'matched_buffer':
            raise ValueError('Original compiler selection requires a matched buffer condition')
        if profile['kind'] == 'matched_buffer':
            from .baselines import tasks as matched
            compiler = profile.get('original_compiler')
            language = profile.get('output_language')
            if (not isinstance(compiler, str) or compiler not in matched.LANGUAGES
                    or language not in matched.LANGUAGES[compiler]
                    or profile.get('condition') not in ('recover', 'codegen')
                    or profile['id'] != compiler + '_' + language + '_' + profile['condition']
                    or any(key in profile for key in ('variant', 'conventions', 'families'))):
                raise ValueError('Unsupported matched compiler/language condition')
            expected = {matched.FILENAMES[language]: 1}
        elif profile['kind'] == 'localization':
            expected, language = {'locations.json': 1}, None
        elif profile['kind'] == 'c_buffer':
            expected, language = {'solution.c': 1}, 'c'
        elif profile['kind'] == 'cpp_buffer':
            if profile['id'] not in ('cpp_recover', 'cpp_codegen'):
                raise ValueError('Unknown original-language buffer condition')
            expected, language = {'solution.cpp': 1}, 'cpp'
        elif profile['kind'] in ('c_lifecycle', 'cpp_lifecycle'):
            from . import lifecycle_tasks, lifecycle
            _, language, filename = lifecycle_tasks.profile_contract(profile)
            expected = {filename: 1}
            if (not isinstance(profile.get('families'), list) or not profile['families']
                    or len(profile['families']) != len(set(profile['families']))
                    or any(name not in lifecycle.FAMILIES for name in profile['families'])
                    or any(key in profile for key in ('conventions', 'condition', 'variant'))):
                raise ValueError('Unsupported lifecycle family selection')
        elif profile['kind'] in ('optimized_localization', 'native_abi'):
            optimized = profile['kind'] == 'optimized_localization'
            variants = ({'optimized_recover': 'optimized', 'lto_recover': 'lto'} if optimized else
                        {'rust_native_recover': 'rust', 'go_internal_recover': 'go'})
            if (profile['id'] not in variants or profile.get('variant') != variants[profile['id']]
                    or profile.get('families') != ['dispatch_registry' if optimized else 'variable_objects']
                    or any(key in profile for key in ('condition', 'conventions'))):
                raise ValueError('Unsupported optimized/native capability selection')
            expected, language = {'instances.json' if optimized else 'native-abi.json': 1}, None
        elif profile['kind'] == 'indirect_call':
            variants = {'indirect_recover': 'baseline', 'indirect_recover_counterfactual': 'reordered'}
            if (profile['id'] not in variants or profile.get('variant') != variants[profile['id']]
                    or profile.get('families') != ['callback_policy']
                    or any(key in profile for key in ('condition', 'conventions'))):
                raise ValueError('Unsupported original indirect-call selection')
            expected, language = {'calls.json': 1}, None
        elif profile['kind'] == 'variant_object':
            variants = {'variant_recover': 'baseline', 'variant_recover_counterfactual': 'reordered'}
            if (profile['id'] not in variants or profile.get('variant') != variants[profile['id']]
                    or profile.get('families') != ['variable_objects']
                    or any(key in profile for key in ('condition', 'conventions'))):
                raise ValueError('Unsupported original variant-object selection')
            expected, language = {'types.json': 1}, None
        elif profile['kind'] == 'pointer_probe':
            variants = {'pointer_recover': 'baseline', 'pointer_recover_counterfactual': 'reordered',
                        'pointer_topology_recover': 'indirect',
                        'pointer_topology_recover_counterfactual': 'indirect_reordered'}
            if (profile['id'] not in variants or profile.get('variant') != variants[profile['id']]
                    or profile.get('families') != ['variable_objects']
                    or any(key in profile for key in ('condition', 'conventions'))):
                raise ValueError('Unsupported original pointer-object selection')
            expected, language = {'types.json': 1}, None
        elif profile['kind'] == 'abi_probe':
            from .abi import contracts
            condition = profile.get('condition', profile['id'])
            if (condition not in contracts.CONDITIONS or not isinstance(profile.get('families'), list)
                    or not profile['families'] or len(profile['families']) != len(set(profile['families']))
                    or any(name not in contracts.FAMILIES for name in profile['families'])
                    or not isinstance(profile.get('conventions'), list) or not profile['conventions']
                    or len(profile['conventions']) != len(set(profile['conventions']))
                    or any(name not in contracts.CONVENTIONS for name in profile['conventions'])):
                raise ValueError('Unsupported type/ABI profile selection')
            if 'variant' in profile:
                from .abi import variants
                if variants.condition(profile['variant']) != condition:
                    raise ValueError('Counterfactual and scoring condition disagree')
                for family in profile['families']: variants.select(family, profile['variant'])
            elif condition != profile['id'] or 'condition' in profile:
                raise ValueError('A separate scoring condition requires a reviewed counterfactual')
            expected, language = {'abi.json' if condition == 'abi_recover' else 'types.json': 1}, None
        else:
            raise ValueError('Unimplemented capability profile cannot be enabled')
        if (profile['artifact_schema'] != expected or profile['output_language'] != language
                or any(type(version) is not int for version in profile['artifact_schema'].values())):
            raise ValueError('Profile artifacts do not match their evaluator')
        if profile['kind'] not in ('abi_probe', 'pointer_probe', 'variant_object', 'indirect_call', 'optimized_localization', 'native_abi', 'c_lifecycle', 'cpp_lifecycle', 'matched_buffer') and any(key in profile for key in ('families', 'conventions', 'condition', 'variant')):
            raise ValueError('Family/ABI selectors apply only to original-endpoint conditions')
    families = json.loads((ROOT / value['families_file']).read_bytes())
    if families.get('schema_version') != 1 or families.get('inference_enabled') is not False:
        raise ValueError('Family definitions must remain inference-disabled')
    names = [row['id'] for row in families['families']]
    if len(names) != len(set(names)) or not names:
        raise ValueError('Program families must be unique and nonempty')
    for name in names:
        identifier(name)
    return value, families
