"""Allowlisted result cache and HTML-only publication; never launch a provider."""
from contextlib import ExitStack
import copy
import datetime
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import os
import shutil
import socket
import subprocess
from unittest.mock import patch
from string import Template
import tempfile
import records
import action_cache
import report_prices
import report_views
import i18n_report
import report_budget
import localization_report
import report_reader
import report_reader_summary
from report_labels import route_label
from report_states import STATES
from report_io import metadata_snapshot
from capabilities import summary as capability_summary
from capabilities import measurements as capability_measurements
from capabilities import registration_report
from urllib.parse import unquote, urlsplit

from report import Report, ASSETS, escape, json_text, money, summary, table
import report_charts as charts

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / 'benchmarks/public/summary.json'
OUTPUT = ROOT / 'benchmarks/site'
SECRET_PATTERNS = [
    r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----',
    r'\bsk-[A-Za-z0-9_-]{24,}',
    r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})',
    r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}',
    r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', r'\bAIza[0-9A-Za-z_-]{35}\b',
    r'/mnt/[a-z]/|/home/|file://|[A-Za-z]:\\Users\\',
]


def privacy_check(text):
    if any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
        raise ValueError('Publication rejected: credential or private-path pattern')


def pick(value, names):
    return {name: value.get(name) for name in names.split()}


CASE = 'id language suite label tool_count max_tool_calls timeout_seconds max_output_tokens standard string_abi'
ROW = 'experiment experiment_label role model model_label harness status condition condition_label output_language points_total points_passed program_success'
BUILD = 'compiler_family compiler_version optimization standard string_abi standard_library strip_mode'
POINT = 'suite base_case compiler_family compiler_id compiler_version optimization standard string_abi strip_mode status inputs_current checks'
FEATURE = 'feature compiler_id compiler_version standard status inputs_current model_attempts'
USAGE = 'inputTokens cacheReadTokens cacheWriteTokens outputTokens totalTokens'
RUBRIC = ('names', 'decomposition', 'language_idioms', 'formatting', 'explanatory_comments')


def project_row(row):
    out = pick(row, ROW)
    if 'attempt_current' in row:
        out.update(attempt_current=row['attempt_current'], attempt_id=row.get('attempt_id'))
    if 'failure_receipt_malformed' in row:
        out['failure_receipt_malformed'] = row['failure_receipt_malformed']
    out['case'] = pick(row['case'], CASE)
    out['source'] = bool(row.get('source'))  # Availability only; no path or code.
    out['environment_delivery'] = row.get('environment_delivery', 'unverified')
    out['runtime_attestation_status'] = row.get('runtime_attestation_status', 'historical_unknown' if row.get('receipt') else 'not_recorded')
    out['target_build'] = pick(row['target_build'], BUILD) if row.get('target_build') else None
    if 'budget_condition' in row:
        out['budget_condition'] = report_budget.project(row['budget_condition'])
    out['receipt'] = None
    if row.get('receipt'):
        receipt = row['receipt']
        out['receipt'] = pick(receipt, 'elapsed_seconds elapsed_ms timed_out tool_calls tool_errors')
        out['receipt']['timed_out'] = bool(receipt.get('timed_out'))
        coverage = report_prices.coverage(receipt)
        out['receipt']['reported_usage'] = {k: (receipt.get('reported_usage') or {}).get(k,
            0 if coverage['proven_zero_forwards'] else None) for k in USAGE.split()}
        out['receipt']['usage_coverage'] = coverage
    if row.get('pricing_basis_id'):
        out['pricing_basis_id'] = row['pricing_basis_id']
    out['cost'] = pick(row['cost'], 'low_usd high_usd usage_complete')
    out['cost']['usage_status'] = row['cost'].get('usage_status', 'legacy_unknown')
    if 'periods' in row['cost']:
        out['cost']['periods'] = list(row['cost']['periods'])
    out['grade'] = None
    if row.get('grade'):
        grade = row['grade']
        out['grade'] = pick(grade, 'compiled passed total')
        out['grade'].update(original_passed=grade.get('original_passed', grade['passed']),
                            original_total=grade.get('original_total', grade['total']),
                            original_program_success=grade.get('original_program_success', row['program_success']),
                            grading_version=grade['grading_version'] if grade.get('grading_version') in ('exact-returns-v2', 'exact-returns-v3') else 'legacy',
                            return_provenance='native_process_unauthenticated',
                            grading_runtime_status=('verified_closed_image' if grade.get('grading_runtime', {}).get('id') else
                                                    grade.get('grading_runtime_status', 'not_executed' if grade.get('grading_version') == 'exact-returns-v3' else 'historical_limited')),
                            grading_runtime_id=grade.get('grading_runtime', {}).get('id', grade.get('grading_runtime_id', '')))
        # Failure text may contain provider output. Publish categories only.
        out['grade']['failure'] = ('Expected single native block' if 'Expected' in (grade.get('failure') or '') else
                                  'Compilation failed' if not grade['compiled'] else
                                  'Behavior mismatch' if not row['program_success'] else '')
        out['grade']['points'] = {k: pick(v, 'passed total') for k, v in grade.get('points', {}).items()}
    review = row.get('readability')
    out['readability'] = {'total': review['total'], 'scores': {k: review['scores'][k] for k in RUBRIC}} if review else None
    return out


def project(report):
    data = report.data
    performance = data.get('runtime_performance', {})
    warm = performance.get('warm')
    cold = performance.get('cold')
    safe_performance = {
        'warm': ({**pick(warm, 'measured_at total_seconds budget_seconds prepared_cases native_saved_cases reusable_native_cases retained_changed_input_cases accepted_regression_checks verification_cache_hit current passed model_api_calls provider_launches'),
                  'stages': [pick(row, 'name seconds') for row in warm['stages']]} if warm else None),
        'cold': ({**pick(cold, 'measured_at total_seconds passed complete'),
                  'stages': [pick(row, 'name seconds exit_code') for row in cold['stages']]} if cold else None)}
    benchmark = performance.get('benchmark')
    if benchmark:
        safe_performance['benchmark'] = {
            **pick(benchmark, 'schema_version mode status total_seconds selected_tasks cache_hits provider_launches model_api_calls failures unprocessed_tasks'),
            **({'queue_holds': benchmark['queue_holds']} if 'queue_holds' in benchmark else {}),
            'stages': [pick(row, 'stage status elapsed_seconds') for row in benchmark['stages']]}
    original = data['environment_audit'].get('cases', [])
    fixed = data['condition_evidence_audit'].get('cases', [])
    projected = {
        'schema_version': 10, 'data_as_of': data['data_as_of'],
        'runtime_performance': safe_performance,
        'task_budget_policy': copy.deepcopy(data['task_budget_policy']),
        'source_report_build_id': report.build_id,
        'models': {k: pick(v, 'id label harness') for k, v in data['models'].items()},
        'experiments': [pick(e, 'id label role suite') for e in data['experiments']],
        'conditions': {'active_case_conditions': data['conditions']['active_case_conditions']},
        'records': [project_row(r) for r in data['records']],
        'condition_records': [project_row(r) for r in data['condition_records']],
        'runtime_attestation': pick(data['runtime_attestation'], 'status inputs_current model_api_calls runtimes controls_passed controls_current checks'),
        'grading_runtime': {**pick(data['grading_runtime'], 'status controls_passed controls_current checks model_api_calls'),
                            'profiles': {language: pick(profile, 'id files bytes compiler_version')
                                         for language, profile in data['grading_runtime']['profiles'].items()}},
        'c_executor': pick(data['c_executor'], 'status controls_passed controls_current checks failed model_api_calls engine_version engine_id base_image module_files module_bytes'),
        'capability_preparation': {**pick(data['capability_preparation'],
            'status inference_enabled families task_definitions frozen_tasks frozen_inputs_current model_api_calls supported_capabilities source_languages'),
            'controls': [pick(row, 'id passed current checks failed') for row in data['capability_preparation']['controls']]},
        'capability_measurements': capability_measurements.public_projection(data['capability_measurements']),
        'capability_registration': registration_report.public_projection(data['capability_registration']),
        'audit': {
            'original_cases': len(original),
            'original_spilled_results': sum(c.get('counts', {}).get('spilled', 0) for c in original),
            'original_blocked_recoveries': sum(c.get('blocked_spill_recovery', 0) for c in original),
            'fixed_verified_cases': sum(c.get('inputs_current') and c.get('status') == 'primary_complete' for c in fixed),
            'fixed_audited_cases': len(fixed),
            'paging_offline_verified': bool(data['paging_validation'].get('passed') and data['paging_validation'].get('inputs_current')),
        },
        'glm_diagnostics': [pick(c, 'experiment case status timed_out elapsed_seconds reasoning_chars content_chars tool_calls request_count http_errors repeated_successful_queries tool_seconds_sum') for c in data['glm_diagnosis']['cases']],
        'compiler_points': [pick(p, POINT) for p in data['cpp_build_points']['points']],
        'feature_probes': [pick(p, FEATURE) for p in data['cpp_build_points']['language_feature_probes']['probes']],
    }
    if data.get('localization_regrades'):
        projected['localization_regrades'] = copy.deepcopy(data['localization_regrades'])
    if 'price_bases' in data:
        projected['price_bases'] = [project_price_basis(value) for value in data['price_bases']]
    if 'typed_budget_conditions' in data:
        projected['typed_budget_conditions'] = {key: report_budget.project(value)
            for key, value in data['typed_budget_conditions'].items()}
    return projected


# A recursive schema rejects unknown fields before publication, including fields manually
# added to the public cache. Free-form answers, errors and provenance are absent.
NUMBER = (int, float, type(None))
TEXT = (str, type(None))
MAP = lambda schema: ('map', schema)
OPTIONAL = lambda schema: ('optional', schema)
GRADE = {'compiled': bool, 'passed': int, 'total': int, 'failure': str,
         'points': MAP({'passed': int, 'total': int})}
ROW_SCHEMA = {**{k: TEXT for k in ROW.split()},
              'attempt_current': OPTIONAL(bool), 'attempt_id': OPTIONAL(TEXT),
              'failure_receipt_malformed': OPTIONAL(bool),
              'points_total': NUMBER, 'points_passed': NUMBER, 'program_success': bool,
              'case': {**{k: TEXT for k in CASE.split()}, **{k: NUMBER for k in 'tool_count max_tool_calls timeout_seconds max_output_tokens string_abi'.split()}},
              'source': bool, 'environment_delivery': str,
              'target_build': ({**{k: TEXT for k in BUILD.split()}, 'string_abi': NUMBER}, type(None)),
              'receipt': ({**{k: NUMBER for k in 'elapsed_seconds elapsed_ms tool_calls tool_errors'.split()}, 'timed_out': (bool, type(None)), 'reported_usage': {k: NUMBER for k in USAGE.split()}}, type(None)),
              'cost': {'low_usd': NUMBER, 'high_usd': NUMBER, 'usage_complete': bool},
              'grade': (GRADE, type(None)),
              'readability': ({'total': NUMBER, 'scores': {k: int for k in RUBRIC}}, type(None))}
SCHEMA = {
    'schema_version': int, 'data_as_of': TEXT, 'source_report_build_id': str,
    'models': MAP({k: str for k in 'id label harness'.split()}),
    'experiments': [{k: str for k in 'id label role suite'.split()}],
    'conditions': {'active_case_conditions': MAP(str)},
    'records': [ROW_SCHEMA], 'condition_records': [ROW_SCHEMA],
    'audit': {**{k: int for k in 'original_cases original_spilled_results original_blocked_recoveries fixed_verified_cases fixed_audited_cases'.split()}, 'paging_offline_verified': bool},
    'glm_diagnostics': [{**{k: NUMBER for k in 'elapsed_seconds reasoning_chars content_chars tool_calls request_count http_errors repeated_successful_queries tool_seconds_sum'.split()}, **{k: str for k in 'experiment case status'.split()}, 'timed_out': bool}],
    'compiler_points': [{**{k: TEXT for k in POINT.split()}, 'inputs_current': bool, 'checks': NUMBER, 'string_abi': NUMBER}],
    'feature_probes': [{**{k: TEXT for k in FEATURE.split()}, 'inputs_current': bool, 'model_attempts': int}],
}

# Accept the existing sanitized snapshot without retroactively certifying it.
LEGACY_SCHEMA = copy.deepcopy(SCHEMA)
ROW_SCHEMA['cost']['usage_status'] = str
GRADE.update(original_passed=NUMBER, original_total=NUMBER, original_program_success=bool,
             grading_version=str, return_provenance=str)
V2_SCHEMA = copy.deepcopy(SCHEMA)
ROW_SCHEMA['runtime_attestation_status'] = str
SCHEMA['runtime_attestation'] = {'status': str, 'inputs_current': bool, 'model_api_calls': int, 'controls_passed': bool, 'controls_current': bool, 'checks': int,
    'runtimes': MAP({'identity': str, 'components': MAP({'sha256': str, 'files': int, 'bytes': int})})}
V3_SCHEMA = copy.deepcopy(SCHEMA)
GRADE.update(grading_runtime_status=str, grading_runtime_id=str)
SCHEMA['grading_runtime'] = {'status': str, 'controls_passed': bool, 'controls_current': bool, 'checks': int,
    'model_api_calls': int, 'profiles': MAP({'id': str, 'files': int, 'bytes': int, 'compiler_version': str})}
V4_SCHEMA = copy.deepcopy(SCHEMA)
SCHEMA['c_executor'] = {**{key: str for key in ('status', 'engine_version', 'engine_id', 'base_image')},
                        'controls_passed': bool, 'controls_current': bool,
                        **{key: int for key in ('checks', 'failed', 'model_api_calls', 'module_files', 'module_bytes')}}
V5_SCHEMA = copy.deepcopy(SCHEMA)
SCHEMA['capability_preparation'] = {'status': str, 'inference_enabled': bool, 'families': int,
    'task_definitions': int, 'frozen_tasks': int, 'frozen_inputs_current': bool, 'model_api_calls': int,
    'supported_capabilities': [str], 'source_languages': [str],
    'controls': [{'id': str, 'passed': bool, 'current': bool, 'checks': int, 'failed': int}]}
V6_SCHEMA = copy.deepcopy(SCHEMA)
TYPED_COST = {'low_usd': NUMBER, 'high_usd': NUMBER, 'usage_complete': bool}
TYPED_GRADE = {'grading_id': str, 'grading_version': str, 'passed': int, 'total': int,
    'artifact_valid': bool, 'task_success': bool, 'return_provenance': str,
    'scores': MAP({'score': NUMBER, 'components': MAP(NUMBER)})}
TYPED_OBSERVATION = {**{key: str for key in ('attempt_id', 'model', 'profile_id', 'task_id', 'family_id', 'lineage_id',
    'build_id', 'binary_sha256', 'source_language', 'hint_profile', 'analysis_condition',
    'public_package_sha256', 'status', 'usage_status', 'runtime_status', 'budget_status', 'accounting_policy')},
    'output_language': TEXT, 'capability_ids': [str], 'grade': (TYPED_GRADE, type(None)),
    'grade_revisions': [{'grading_id': str, 'grading_version': str, 'passed': int, 'total': int, 'task_success': bool, 'selected': bool}],
    'invalid_grade_revisions': int, 'invalid_accounting_revisions': int, 'record_valid': bool,
    'readability': {'applicable': bool, 'total': NUMBER, 'provenance': str},
    'known_tokens': MAP(int), 'cost': TYPED_COST,
    'accounting_revisions': [{'accounting_id': str, 'version': str, 'usage_status': str, 'low_usd': NUMBER, 'high_usd': NUMBER}]}
SCHEMA['capability_measurements'] = {
    'schema_version': int, 'schedule_status': str, 'schedule_id': str, 'inference_enabled': bool,
    **{key: int for key in ('declared_tasks', 'configured_cells', 'scheduled_cells', 'disabled_cells', 'saved_observations',
                           'duplicate_saved_copies', 'invalid_attempt_records', 'released_capabilities')},
    'statuses': MAP(int), 'validation': {'passed': bool, 'current': bool, 'checks': int, 'model_api_calls': int},
    'cells': [{**{key: str for key in ('cell_id', 'model', 'task_id', 'family_id', 'lineage_id', 'profile_id', 'preparation_status',
                                    'status', 'source_language', 'hint_profile', 'analysis_condition')},
               'output_language': TEXT, 'binary_sha256': TEXT, 'public_package_sha256': TEXT, 'build_id': TEXT, 'capability_ids': [str],
               'scheduled': bool, 'observation': (TYPED_OBSERVATION, type(None))}],
    'history': [TYPED_OBSERVATION],
    'macro_vectors': [{**{key: str for key in ('model', 'profile_id', 'capability_id')},
                       **{key: int for key in ('scheduled_cells', 'graded_cells', 'metric_observations', 'scheduled_families',
                                              'observed_families', 'observed_lineages', 'matched_cells', 'matched_families')},
                       **{key: NUMBER for key in ('observed_macro', 'complete_macro', 'matched_macro')}}],
    'totals': [{'model': str, **{key: int for key in ('configured_cells', 'scheduled_cells', 'graded_cells', 'artifact_valid_cells',
                                                   'successful_cells', 'saved_observations', 'readability_n')},
                'readability_mean': NUMBER, 'known_tokens': {key: NUMBER for key in USAGE.split()}, 'cost': TYPED_COST}]}
V7_SCHEMA = copy.deepcopy(SCHEMA)
ORIGINAL_BUILD = {key: str for key in ('build_id', 'binary_sha256', 'compiler_family', 'compiler_version',
    'compiler_image_id', 'compiler_executable_sha256', 'source_language', 'architecture', 'standard', 'optimization',
    'strip_mode', 'standard_library', 'link_mode', 'binutils_image_id', 'flag_summary_sha256', 'abi')}
ORIGINAL_BUILD['flag_summary'] = [str]
SCHEMA['capability_registration'] = {'status': str, 'policy_id': str, 'data_as_of': TEXT,
    'invalid_records': int, 'control_records_excluded': int,
    'validation': {'passed': bool, 'current': bool, 'checks': int, 'model_api_calls': int},
    'builds': [{'task_id': str, 'family_id': str, 'profile_id': str, 'metadata': ORIGINAL_BUILD}],
    'schedules': [{'registration_id': str, 'registered_at': str, 'scheduled_cells': int, 'prospective_cells': int,
        'prior_cells': int, 'native_context_cells': int, 'policy_id': str, 'parallel_models': int,
        'bindings': [{'binding_id': str, 'created_at': str, 'bound_cells': int, 'dispatch_intents': int,
                      'runtime_ids': [str], 'provider_ids': [str]}]}],
    'observations': [{'attempt_id': str, 'registration_id': TEXT, 'binding_id': TEXT, 'status': str, 'registered_at': TEXT,
                      'admission_mode': OPTIONAL(str)}]}
V8_SCHEMA = copy.deepcopy(SCHEMA)
SCHEMA['task_budget_policy'] = ({'policy_id': str, 'limits': {key: int for key in (
    'max_model_turns', 'max_response_tokens', 'max_output_tokens', 'max_observed_total_tokens')},
    'wallets': OPTIONAL([{'model': str, 'currency': str, 'limit_nano': int, 'semantics': str,
                         'max_response_tokens': OPTIONAL(int)}]),
    'adaptive_budget': OPTIONAL({'id': str, 'condition': str, 'multiplier': int, 'max_response_tokens': int,
        'max_model_turns': (int, type(None)), 'basic_suites': [str], 'sample_policy': str, 'bootstrap': str})}, type(None))
V9_SCHEMA = copy.deepcopy(SCHEMA)
SCHEMA['runtime_performance'] = {
    'warm': ({'measured_at': str, 'total_seconds': (int, float), 'budget_seconds': int,
        **{key: int for key in ('prepared_cases', 'native_saved_cases', 'reusable_native_cases',
            'retained_changed_input_cases', 'accepted_regression_checks', 'model_api_calls', 'provider_launches')},
        **{key: bool for key in ('verification_cache_hit', 'current', 'passed')},
        'stages': [{'name': str, 'seconds': (int, float)}]}, type(None)),
    'cold': ({'measured_at': str, 'total_seconds': (int, float), 'passed': bool, 'complete': bool,
        'stages': [{'name': str, 'seconds': (int, float), 'exit_code': int}]}, type(None))}
BENCHMARK_TIMING_SCHEMA = {
    'schema_version': int, 'mode': str, 'status': str, 'total_seconds': (int, float),
    **{key: int for key in ('selected_tasks', 'cache_hits', 'provider_launches', 'failures', 'unprocessed_tasks')},
    'model_api_calls': (int, type(None)),
    'queue_holds': OPTIONAL(int),
    'stages': [{'stage': str, 'status': str, 'elapsed_seconds': NUMBER}]}

# Optional additive fields preserve old sanitized snapshots without certifying
# missing pricing or request coverage. Unknown categories remain nullable.
USAGE_COVERAGE = {'recorded_requests': (int, type(None)),
                 'requests_with_incomplete_usage': (int, type(None)), 'proven_zero_forwards': bool}
ROW_SCHEMA['receipt'][0]['usage_coverage'] = OPTIONAL(USAGE_COVERAGE)
ROW_SCHEMA['pricing_basis_id'] = OPTIONAL(str)
ROW_SCHEMA['cost']['periods'] = OPTIONAL([str])
PRICE_BASIS = {**{key: TEXT for key in ('snapshot_at', 'currency', 'source_url', 'fx_date', 'fx_source_url', 'request_first_at', 'request_last_at')},
    **{key: str for key in ('id', 'model', 'snapshot_sha256', 'rule', 'estimate_kind')},
    'unit_tokens': int, 'cny_per_usd': NUMBER, 'short_context_max_input': (int, type(None)),
    'rates': [{'period': str, **{key: NUMBER for key in report_prices.RATE_KEYS}}],
    'peak_utc_hours': [[int]], 'recorded_periods': [str], 'actual_invoice_available': bool}
SCHEMA['price_bases'] = OPTIONAL([PRICE_BASIS])
BUDGET_OUTCOME = {'status': str, 'budget_exceeded': bool, 'queue_stop_required': bool,
                  'queue_stop_reason': TEXT}
BUDGET_CONDITION = {'mode': str, 'evidence': str, 'protocol': TEXT,
    'provider_output_cap_enforced': (bool, type(None)),
    **{key: NUMBER for key in report_budget.NUMBERS}, 'outcome': OPTIONAL(BUDGET_OUTCOME),
    'wallet': OPTIONAL({'currency': str, 'limit_nano': (int, type(None)), 'semantics': str}),
    'response_allocation': OPTIONAL(str), 'observed_response_output_tokens': OPTIONAL(int),
    'adaptive': OPTIONAL({'mode': str, 'metric': str, 'sample_count': int, 'multiplier': int,
                         'limit': (int, type(None)), 'baseline_sha256': str})}
ROW_SCHEMA['budget_condition'] = OPTIONAL(BUDGET_CONDITION)
SCHEMA['typed_budget_conditions'] = OPTIONAL(MAP(BUDGET_CONDITION))
TYPED_OBSERVATION['budget_outcome'] = OPTIONAL(BUDGET_OUTCOME)
SCHEMA['localization_regrades'] = OPTIONAL({'policy': str, 'updated_at': str, 'entries': [{
    'revision_id': str, 'original_grading_id': str, 'answer_sha256': str,
    'normalized_sha256': TEXT, 'transforms': int, 'reason': str,
    'observation': TYPED_OBSERVATION, 'grade': (TYPED_GRADE, type(None))}]})


def project_price_basis(value):
    result = pick(value, ' '.join(PRICE_BASIS))
    result['rates'] = [pick(row, 'period ' + ' '.join(report_prices.RATE_KEYS)) for row in value['rates']]
    result['peak_utc_hours'] = [list(hours) for hours in value['peak_utc_hours']]
    return result


def validate_benchmark_timing(value):
    stages = value['stages']
    allowed = {'selection', 'preparation_inspection', 'action_resolution', 'schedule', 'preparation',
               'prepared_action_resolution', 'binding', 'execute', 'finalization'}
    if (value['schema_version'] != 1 or value['mode'] not in ('offline', 'live')
            or value['status'] not in ('running', 'complete', 'incomplete', 'stopped',
                                       'offline_cache_miss', 'pre_dispatch_failure')
            or value['total_seconds'] > 604800 or value['selected_tasks'] > 100000
            or len(stages) > len(allowed) or len({row['stage'] for row in stages}) != len(stages)
            or any(row['stage'] not in allowed or row['status'] not in
                   ('running', 'complete', 'failed', 'interrupted', 'skipped')
                   or (row['elapsed_seconds'] is None) != (row['status'] == 'running')
                   or row['status'] == 'skipped' and row['elapsed_seconds'] != 0 for row in stages)
            or sum(row['elapsed_seconds'] or 0 for row in stages) > value['total_seconds'] + 0.001
            or any(value[key] > value['selected_tasks'] for key in
                   ('cache_hits', 'provider_launches', 'unprocessed_tasks'))
            or value['failures'] > 100000
            or value['cache_hits'] + value['unprocessed_tasks'] > value['selected_tasks']
            or value['model_api_calls'] is not None and value['model_api_calls'] > 10**9
            or value['mode'] == 'offline' and (value['provider_launches'] != 0 or value['model_api_calls'] != 0)
            or value['status'] == 'offline_cache_miss' and (value['mode'] != 'offline' or not value['failures'])
            or value['status'] == 'pre_dispatch_failure' and value['provider_launches'] != 0
            or value.get('queue_holds', 0) > value['selected_tasks']
            or value['status'] == 'complete' and (value['failures'] or value.get('queue_holds') or value['unprocessed_tasks'])):
        raise ValueError('Invalid benchmark stage timing measurement')


def validate_value(value, schema):
    if isinstance(schema, tuple) and schema[0] == 'optional':
        validate_value(value, schema[1])
    elif isinstance(schema, tuple) and schema[0] == 'map':
        if not isinstance(value, dict):
            raise ValueError('Expected publication map')
        for key, item in value.items():
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._+/-]{0,100}', key):
                raise ValueError('Invalid publication map key')
            validate_value(item, schema[1])
    elif isinstance(schema, tuple):
        for option in schema:
            try:
                validate_value(value, option)
                return
            except (ValueError, TypeError):
                pass
        raise ValueError('Invalid nullable publication value')
    elif isinstance(schema, dict):
        required = {key for key, spec in schema.items() if not (isinstance(spec, tuple) and spec[0] == 'optional')}
        if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(schema):
            raise ValueError('Unexpected or missing publication fields')
        for k, spec in schema.items():
            if k in value:
                validate_value(value[k], spec)
    elif isinstance(schema, list):
        if not isinstance(value, list):
            raise ValueError('Expected publication list')
        for item in value:
            validate_value(item, schema[0])
    elif type(value) is not schema:
        raise ValueError('Invalid publication scalar type')
    elif isinstance(value, str) and (len(value) > 200 or '\n' in value or '\r' in value):
        raise ValueError('Unbounded publication text')
    elif isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
        raise ValueError('Invalid publication number')


def validate(data):
    if data.get('schema_version') == 1:
        validate_value(data, LEGACY_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 2
        for row in data['records'] + data['condition_records']:
            row['cost']['usage_status'] = 'legacy_unknown'
            if row['grade']:
                grade = row['grade']
                grade.update(original_passed=grade['passed'], original_total=grade['total'],
                             original_program_success=row['program_success'], grading_version='legacy',
                             return_provenance='native_process_unauthenticated')
    if data.get('schema_version') == 2:
        validate_value(data, V2_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 3
        data['runtime_attestation'] = {'status': 'not_recorded', 'inputs_current': False, 'model_api_calls': 0, 'runtimes': {}, 'controls_passed': False, 'controls_current': False, 'checks': 0}
        for row in data['records'] + data['condition_records']:
            row['runtime_attestation_status'] = 'historical_unknown' if row['receipt'] or row['status'] != 'not_started' else 'not_recorded'
    if data.get('schema_version') == 3:
        validate_value(data, V3_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 4
        data['grading_runtime'] = {'status': 'not_recorded', 'controls_passed': False, 'controls_current': False,
                                   'checks': 0, 'model_api_calls': 0, 'profiles': {}}
        for row in data['records'] + data['condition_records']:
            if row['grade']:
                row['grade'].update(grading_runtime_status='historical_limited', grading_runtime_id='')
    if data.get('schema_version') == 4:
        validate_value(data, V4_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 5
        data['c_executor'] = {'status': 'not_recorded', 'controls_passed': False, 'controls_current': False,
                              'checks': 0, 'failed': 0, 'model_api_calls': 0, 'engine_version': '',
                              'engine_id': '', 'base_image': '', 'module_files': 0, 'module_bytes': 0}
    if data.get('schema_version') == 5:
        validate_value(data, V5_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 6
        data['capability_preparation'] = capability_summary.empty()
    if data.get('schema_version') == 6:
        validate_value(data, V6_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 7
        data['capability_measurements'] = capability_measurements.empty()
    if data.get('schema_version') == 7:
        validate_value(data, V7_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 8
        data['capability_registration'] = registration_report.empty()
    if data.get('schema_version') == 8:
        validate_value(data, V8_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 9
        data['task_budget_policy'] = None
    if data.get('schema_version') == 9:
        validate_value(data, V9_SCHEMA)
        data = copy.deepcopy(data)
        data['schema_version'] = 10
        data['runtime_performance'] = {'warm': None, 'cold': None}
    schema = SCHEMA
    benchmark = data.get('runtime_performance', {}).get('benchmark')
    if 'benchmark' in data.get('runtime_performance', {}):
        # An optional addition to v10: historical snapshots retain their exact
        # two-field timing inventory and receive no invented batch observation.
        schema = {**SCHEMA, 'runtime_performance': {
            **SCHEMA['runtime_performance'], 'benchmark': BENCHMARK_TIMING_SCHEMA}}
    validate_value(data, schema)
    if data['schema_version'] != 10 or not re.fullmatch('[0-9a-f]{64}', data['source_report_build_id']):
        raise ValueError('Unknown public cache version or build identity')
    if benchmark is not None:
        validate_benchmark_timing(benchmark)
    warm = data['runtime_performance']['warm']
    if warm:
        if (warm['model_api_calls'] != 0 or warm['provider_launches'] != 0
                or warm['budget_seconds'] <= 0 or warm['total_seconds'] > warm['budget_seconds']
                or {row['name'] for row in warm['stages']} != {'acceptance_receipts', 'prepared_case_bytes',
                    'saved_native_artifacts', 'fresh_content_seal', 'persistent_action_cache'}
                or len(warm['stages']) != 5
                or sum(row['seconds'] for row in warm['stages']) > warm['total_seconds'] + 0.001):
            raise ValueError('Invalid offline warm performance measurement')
    budget_policy = data['task_budget_policy']
    if budget_policy and (any(n <= 0 for n in budget_policy['limits'].values())
            or budget_policy['limits']['max_response_tokens'] > budget_policy['limits']['max_output_tokens']):
        raise ValueError('Invalid public task spending limits')
    capability_measurements.validate_projection(data['capability_measurements'])
    overlay = data.get('localization_regrades')
    if overlay:
        if overlay['policy'] != localization_report.POLICY:
            raise ValueError('Unknown localization regrade policy')
        seen = set()
        for entry in overlay['entries']:
            row, grade = entry['observation'], entry['grade']
            if (row['attempt_id'] in seen or not row['record_valid'] or not row['grade']
                    or row['grade']['artifact_valid'] or not row['capability_ids']
                    or any(not c.startswith('L') for c in row['capability_ids'])
                    or entry['original_grading_id'] != row['grade']['grading_id']
                    or row['model'] not in data['models'] or entry['transforms'] < 0
                    or row['status'] not in ('graded', 'completed_over_budget', 'completed_queue_held')):
                raise ValueError('Invalid localization regrade binding')
            seen.add(row['attempt_id'])
            for key in ('revision_id', 'original_grading_id', 'answer_sha256'):
                capability_measurements.hash_value(entry[key])
            if entry['normalized_sha256'] is not None:
                capability_measurements.hash_value(entry['normalized_sha256'])
            if grade:
                if (entry['reason'] != 'semantic_regraded' or grade['grading_id'] != entry['revision_id']
                        or grade['grading_version'] != localization_report.POLICY
                        or grade['artifact_valid'] is not True or not 0 <= grade['passed'] <= grade['total']
                        or grade['total'] <= 0 or grade['task_success'] != (grade['passed'] == grade['total'])
                        or set(grade['scores']) != set(row['capability_ids'])):
                    raise ValueError('Invalid semantic localization regrade')
            elif entry['reason'] not in ('format_not_losslessly_convertible', 'target_or_contract_not_admitted'):
                raise ValueError('Unknown localization review reason')
    for row in data['records'] + data['condition_records']:
        if 'budget_condition' in row:
            report_budget.validate(row['budget_condition'])
    typed_ids = {row['attempt_id'] for row in data['capability_measurements']['history']}
    for key, value in data.get('typed_budget_conditions', {}).items():
        if key not in typed_ids or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Budget condition references an unknown typed attempt')
        report_budget.validate(value)
    registration_report.validate(data['capability_registration'])
    if any(row['model'] not in data['models'] for row in data['capability_measurements']['cells']):
        raise ValueError('Unknown configured model in typed measurement inventory')
    if data['data_as_of']:
        datetime.datetime.fromisoformat(data['data_as_of'])
    price_ids = set()
    for item in data.get('price_bases', []):
        if (item['id'] in price_ids or item['model'] not in data['models']
                or not re.fullmatch('[0-9a-f]{64}', item['id'])
                or not re.fullmatch('[0-9a-f]{64}', item['snapshot_sha256'])
                or item['currency'] not in ('USD', 'CNY') or not 0 < item['unit_tokens'] <= 10**9
                or item['actual_invoice_available'] is not False
                or item['estimate_kind'] not in ('api_equivalent', 'published_rate_estimate')
                or not 1 <= len(item['rates']) <= 4):
            raise ValueError('Invalid sanitized pricing basis')
        price_ids.add(item['id'])
        for key in ('source_url', 'fx_source_url'):
            if item[key] is not None and report_prices.public_url(item[key]) != item[key]:
                raise ValueError('Unsafe pricing source URL')
        for key in ('snapshot_at', 'fx_date', 'request_first_at', 'request_last_at'):
            if item[key] is not None and report_prices._date(item[key]) != item[key]:
                raise ValueError('Invalid pricing date')
        if (item['cny_per_usd'] is not None and item['cny_per_usd'] <= 0
                or any(len(hours) != 2 or not 0 <= hours[0] < hours[1] <= 24 for hours in item['peak_utc_hours'])
                or any(rate['period'] not in ('standard', 'peak', 'off_peak', 'standard_short_context',
                                             'standard_long_context') for rate in item['rates'])):
            raise ValueError('Invalid pricing rate period or conversion')
        if set(item['recorded_periods']) - {'standard', 'peak', 'off_peak', 'standard_short_context', 'standard_long_context'}:
            raise ValueError('Invalid recorded pricing periods')
    runtime = data['runtime_attestation']
    if runtime['status'] not in ('not_recorded', 'local_inventory_only') or runtime['model_api_calls'] != 0 or not set(runtime['runtimes']).issubset({'deepseek-harness', 'zcode'}):
        raise ValueError('Invalid public runtime inventory status')
    grading = data['grading_runtime']
    executor = data['c_executor']
    preparation = data['capability_preparation']
    if (preparation['status'] not in ('not_recorded', 'offline_preparation') or preparation['model_api_calls'] != 0
            or any(not 0 <= preparation[key] <= 10000 for key in ('families', 'task_definitions', 'frozen_tasks'))
            or preparation['frozen_tasks'] > preparation['task_definitions']
            or set(preparation['supported_capabilities']) - {'L1', 'L2', 'L3', 'L4', 'L5', 'L6', 'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'}
            or set(preparation['source_languages']) - {'cpp', 'rust', 'go'}):
        raise ValueError('Invalid capability preparation summary')
    for control in preparation['controls']:
        if (control['id'] not in ('localization', 'structured_runtime', 'c_buffers', 'c_lifecycle', 'cpp_buffers', 'cpp_lifecycle', 'abi_probes', 'pointer_probes', 'variant_objects', 'indirect_calls', 'optimized_locations', 'native_abi', 'coordinator', 'coordinator_incremental', 'matched_baselines', 'rust_buffers', 'go_buffers')
                or not 0 <= control['checks'] <= 10000 or not 0 <= control['failed'] <= 10000
                or control['passed'] and (not control['checks'] or control['failed'])):
            raise ValueError('Invalid capability preparation controls')
    if executor['status'] not in ('not_recorded', 'saved_offline_controls') or executor['model_api_calls'] != 0:
        raise ValueError('Invalid C executor control status')
    if executor['controls_passed'] and (executor['status'] != 'saved_offline_controls' or executor['failed'] or executor['checks'] < 29):
        raise ValueError('Inconsistent C executor acceptance evidence')
    if executor['status'] == 'saved_offline_controls':
        if any(not re.fullmatch('[0-9a-f]{64}', executor[key]) for key in ('engine_id', 'base_image')):
            raise ValueError('Invalid C executor content identity')
    if (grading['status'] not in ('not_recorded', 'saved_offline_controls') or grading['model_api_calls'] != 0
            or not set(grading['profiles']).issubset({'c', 'cpp', 'rust', 'go'})):
        raise ValueError('Invalid public grading runtime status')
    for profile in grading['profiles'].values():
        if not re.fullmatch('[0-9a-f]{64}', profile['id']):
            raise ValueError('Invalid grading runtime content identity')
    for inventory in runtime['runtimes'].values():
        if not re.fullmatch('[0-9a-f]{64}', inventory['identity']):
            raise ValueError('Invalid runtime content identity')
        for component in inventory['components'].values():
            if not re.fullmatch('[0-9a-f]{64}', component['sha256']):
                raise ValueError('Invalid component content digest')
    seen, current_cells = set(), set()
    for row in data['records'] + data['condition_records']:
        for key in ['experiment', 'model', 'condition']:
            if not re.fullmatch('[a-zA-Z0-9_.-]+', row[key]):
                raise ValueError('Unsafe publication identifier')
        if not re.fullmatch('[a-zA-Z0-9_.-]+', row['case']['id']):
            raise ValueError('Unsafe case identifier')
        cell = (row['experiment'], row['case']['id'], row['model'], row['condition'])
        attempt_id = row.get('attempt_id')
        if attempt_id is not None and not re.fullmatch('[0-9a-f]{64}', attempt_id):
            raise ValueError('Invalid publication attempt identity')
        if not row.get('attempt_current', True) and attempt_id is None:
            raise ValueError('Historical publication attempt lacks identity')
        key = (*cell, attempt_id)
        if key in seen:
            raise ValueError('Duplicate publication row')
        seen.add(key)
        if row.get('attempt_current', True):
            if cell in current_cells:
                raise ValueError('Duplicate current publication row')
            current_cells.add(cell)
        if row['model'] not in data['models'] or row['status'] not in STATES:
            raise ValueError('Unknown model or result state')
        budget_outcome = row.get('budget_condition', {}).get('outcome')
        if row['grade'] and budget_outcome and budget_outcome['queue_stop_required'] and row['status'] not in (
                'completed_over_budget', 'completed_queue_held'):
            raise ValueError('Graded native budget stop lost its queue-hold state')
        if row['status'] == 'completed_over_budget' and (
                not budget_outcome or budget_outcome['status'] != 'completed_over_budget'):
            raise ValueError('Over-budget native state lacks saved budget outcome')
        if row['status'] == 'completed_queue_held' and (
                not budget_outcome or not budget_outcome['queue_stop_required'] or budget_outcome['budget_exceeded']):
            raise ValueError('Held native state lacks saved budget outcome')
        if row.get('pricing_basis_id') and row['pricing_basis_id'] not in price_ids:
            raise ValueError('Unknown pricing basis reference')
        if set(row['cost'].get('periods', [])) - {'standard', 'peak', 'off_peak', 'standard_short_context', 'standard_long_context'}:
            raise ValueError('Unknown recorded pricing period')
        receipt = row.get('receipt') or {}
        coverage = receipt.get('usage_coverage')
        if coverage:
            total, incomplete = coverage['recorded_requests'], coverage['requests_with_incomplete_usage']
            if ((total is None) != (incomplete is None) or total is not None and incomplete > total
                    or coverage['proven_zero_forwards'] and any(value not in (None, 0)
                                                               for value in receipt['reported_usage'].values())):
                raise ValueError('Invalid request usage coverage')
        if row['grade'] and row['grade']['passed'] > row['grade']['total']:
            raise ValueError('Invalid behavioral score')
        if row['cost']['usage_status'] not in ('complete', 'partial', 'unknown', 'legacy_unknown'):
            raise ValueError('Invalid usage evidence status')
        if row['runtime_attestation_status'] not in ('historical_unknown', 'verified_at_execution', 'not_recorded', 'unverified'):
            raise ValueError('Invalid runtime attestation status')
        if row['grade'] and (row['grade']['grading_version'] not in ('exact-returns-v2', 'exact-returns-v3', 'legacy') or
                             row['grade']['return_provenance'] != 'native_process_unauthenticated'):
            raise ValueError('Uncertified grading provenance')
        if row['grade']:
            grade = row['grade']
            if grade['grading_runtime_status'] not in ('verified_closed_image', 'historical_limited', 'not_executed'):
                raise ValueError('Invalid grading runtime evidence')
            if grade['grading_runtime_status'] == 'verified_closed_image':
                if not re.fullmatch('[0-9a-f]{64}', grade['grading_runtime_id']) or grade['grading_version'] != 'exact-returns-v3':
                    raise ValueError('Missing closed grading runtime identity')
            elif grade['grading_runtime_id']:
                raise ValueError('Unexpected grading runtime identity')
    privacy_check(json_text(data))
    return data


class PublicReport(Report):
    def __init__(self, output, data):
        self.output, self.data = output, validate(data)
        self.template = Template((ASSETS / 'public-page.html').read_text())
        coverage = ROOT / 'benchmarks/suites/stl-native-v2/config/coverage.json'
        self.coverage = json.loads(coverage.read_text())
        presentation = b''.join((ROOT / 'benchmarks/scripts' / name).read_bytes() for name in
            ('report.py', 'report_charts.py', 'report_views.py', 'report_prices.py', 'report_states.py', 'report_budget.py',
             'report_reader.py', 'report_reader_summary.py', 'report_labels.py'))
        locales = b''.join((ASSETS / 'locales' / (locale + '.json')).read_bytes() for locale in i18n_report.LOCALES)
        self.build_id = hashlib.sha256(json_text(data).encode() + coverage.read_bytes() + Path(__file__).read_bytes() + presentation + locales + (ASSETS / 'public-page.html').read_bytes() + (ASSETS / 'report.css').read_bytes() + (ASSETS / 'report.js').read_bytes() + (ASSETS / 'i18n.js').read_bytes()).hexdigest()
        stamp = data['data_as_of']
        self.data_date = datetime.datetime.fromisoformat(stamp).astimezone(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S JST') if stamp else 'No model measurements recorded'

    def page(self, name, title, body):
        parent = Path(name).parent
        link = lambda target: escape(os.path.relpath(target, str(parent)))
        navigation = [('index.html', 'Overview'), ('comparison.html', 'Model comparison'),
                      ('dimensions.html', 'Capability analysis'), ('cases/index.html', 'Task results'),
                      ('costs.html', 'Cost and time'), ('methodology.html', 'Methods and scope')]
        subtitles = {'index.html': 'Saved correctness results across all configured models and tasks.',
                     'comparison.html': 'Native correctness on shared cases or each model’s measured subset.',
                     'dimensions.html': 'Measured capability profiles with scores and sample counts.',
                     'capabilities.html': 'Localization, reconstruction and type / ABI task results.',
                     'cases/index.html': 'Native language results and typed task results.',
                     'costs.html': 'Recorded usage, published-rate estimates and measured durations.'}
        markup = self.template.substitute(title=escape(title), subtitle=escape(subtitles.get(name, 'Saved results and their interpretation.')),
            stylesheet=link('assets/report.css'), script=link('assets/report.js'),
            navigation=''.join('<a href="' + link(target) + '">' + label + '</a>' for target, label in navigation),
            methods=link('methodology.html'), history=link('history/index.html'),
            locale_scripts=''.join('<script src="' + link('assets/' + asset) + '" defer></script>' for asset in
                ['locales/en.js', 'locales/zh-CN.js', 'locales/ja.js', 'i18n-config.js', 'i18n.js']),
            body=body, data_date=escape(self.data_date), build_id=self.build_id[:12])
        markup = markup.replace('<meta charset="utf-8">', '<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; img-src \'self\' data:; base-uri \'none\'; form-action \'none\'">')
        self.write(name, markup)

    def summary_table(self, rows):
        return table(['Model', 'Graded / configured', 'Programs passed', 'Case-equal mean', 'Check-weighted pass rate'], [
            [escape(item['model_label']) + '<small>' + escape(route_label(item['model'], self.data['models'][item['model']])) + '</small>',
             f'{item["graded_attempts"]}/{item["planned_cells"]}',
             f'{item["programs_passed"]}/{item["graded_attempts"]}' if item['graded_attempts'] else 'Not graded (n=0)',
             f'{item["case_equal_mean"]:.2f}%' if item['case_equal_mean'] is not None else 'Unmeasured',
             f'{100*item["passed"]/item["checks"]:.2f}%' if item['checks'] else 'Unmeasured']
            for item in summary(rows, self.data['models'])])

    def environment_case(self, row):
        return {'status': row['environment_delivery']}

    def overview(self):
        report_reader_summary.overview(self)

    def case_pages(self):
        report_reader.case_pages(self)

    def costs(self):
        report_reader_summary.costs(self)

    def capabilities_page(self):
        report_reader.capabilities(self)

    def capability_inventory_page(self):
        report_reader.capability_inventory(self)

    def methodology(self):
        report_reader.methodology(self)

    def coverage_page(self):
        report_reader.scope(self)

    def history_pages(self):
        report_reader.history(self)

    def build(self):
        for name in ['report.css', 'report.js']:
            self.write('assets/' + name, (ASSETS / name).read_bytes())
        for name, content in i18n_report.assets().items():
            self.write(name, content)
        self.overview()
        self.case_pages()
        self.costs()
        self.capabilities_page()
        self.coverage_page()
        self.history_pages()
        self.methodology()
        self.write('.nojekyll', '')
        validate_site(self.output)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attributes):
        self.targets.extend(v for k, v in attributes if k in ('href', 'src') and v)


@metadata_snapshot()
def validate_site(output):
    output = Path(output).resolve()
    pages, links = 0, 0
    for path in output.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlinks cannot be published')
        if not path.is_file():
            continue
        if path.stat().st_nlink != 1:
            raise ValueError('Hard links cannot be published')
        if path.name == '.nojekyll' and path.stat().st_size:
            raise ValueError('Public marker must be empty')
        if path.name != '.nojekyll' and path.suffix not in ('.html', '.css', '.js'):
            raise ValueError('Non-public file type in deployment')
        text = path.read_text()
        privacy_check(text)
        if path.suffix == '.html':
            pages += 1
            parser = Links()
            parser.feed(text)
            for href in parser.targets:
                url = urlsplit(href)
                if url.scheme or url.netloc:
                    if url.scheme != 'https':
                        raise ValueError('Unexpected external URL')
                    continue
                target = (path.parent / unquote(url.path)).resolve() if url.path else path
                if not target.is_relative_to(output) or not target.is_file():
                    raise ValueError('Broken or escaping publication link')
                links += 1
    if not (output / 'index.html').is_file() or not pages:
        raise ValueError('Public report is incomplete')
    return {'pages': pages, 'local_links': links, 'model_calls': 0}


def denied(*args, **kwargs):
    raise RuntimeError('Public reporting forbids network access and process creation')


def build(output=OUTPUT, snapshot=SNAPSHOT):
    # Building is separate from Git publication. Rendering has no outbound
    # network or child-process path, even if provider credentials exist locally.
    with ExitStack() as guards:
        for obj, name in [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                          (socket, 'create_connection'), (socket, 'getaddrinfo'),
                          (subprocess, 'Popen'), (os, 'system'), (os, 'posix_spawn'),
                          (os, 'execv'), (os, 'execve')]:
            if hasattr(obj, name):
                guards.enter_context(patch.object(obj, name, denied))
        return _build(output, snapshot)


def _build(output=OUTPUT, snapshot=SNAPSHOT):
    data = validate(json.loads(Path(snapshot).read_text()))
    output = Path(output).resolve()
    if output in (ROOT, ROOT / 'benchmarks', ROOT / 'benchmarks/reports') or output.is_relative_to(ROOT / 'benchmarks/runs'):
        raise ValueError('Refusing to replace source or private report output')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rea-public-report-') as tmp:
        stage = Path(tmp) / 'site'
        stage.mkdir()
        report = PublicReport(stage, data)
        action = action_cache.reporting(data, public=True)
        local = action_cache.Cache()
        hit = local.lookup(action)
        if hit:
            local.restore(hit, stage)
        else:
            report.build()
            local.capture(action, stage)
        validate_site(stage)  # Privacy and link checks also apply to cache hits.
        i18n_report.audit(stage)
        with tempfile.TemporaryDirectory(prefix='.public-publish-', dir=output.parent) as publication:
            ready = Path(publication) / 'site'
            shutil.copytree(stage, ready)
            if output.exists():
                if not (output / '.nojekyll').is_file():
                    raise ValueError('Output is not marked as a generated public site')
                output.rename(Path(publication) / 'previous')
            ready.rename(output)
    print('PUBLIC REPORT BUILT:', output, '·', report.build_id[:12], '· no model calls', flush=True)
    return report.build_id


def refresh(report):
    """Refresh from raw local caches only; incomplete clones keep the saved summary."""
    data = validate(project(report))
    # Publication may remove text and paths, never alter any metric or sample.
    for name in ['records', 'condition_records']:
        if summary(report.data[name], report.data['models']) != summary(data[name], data['models']):
            raise ValueError('Public projection changed measured results')
    if SNAPSHOT.exists():
        old = validate(json.loads(SNAPSHOT.read_text()))
        rows = {}
        for r in data['records'] + data['condition_records']:
            rows.setdefault((r['experiment'], r['case']['id'], r['model'], r['condition']), []).append(r)
        complete = all(not r['receipt'] or any(candidate['receipt']
            and (not r['grade'] or candidate['grade'])
            and (not r.get('attempt_id') or r['attempt_id'] == candidate.get('attempt_id'))
            for candidate in rows.get((r['experiment'], r['case']['id'], r['model'], r['condition']), []))
            for r in old['records'] + old['condition_records'])
        if not complete:
            print('PUBLIC CACHE RETAINED: local raw dataset is incomplete', flush=True)
            identity = build()
            stage_pages()
            return identity
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    raw = json_text(data)
    if not SNAPSHOT.exists() or SNAPSHOT.read_text() != raw:
        with tempfile.NamedTemporaryFile(mode='w', dir=SNAPSHOT.parent, delete=False) as handle:
            handle.write(raw)
            temporary = Path(handle.name)
        temporary.replace(SNAPSHOT)
    identity = build()
    stage_pages()
    refresh_readme(data)
    return identity


def refresh_readme(data, path=ROOT / 'README.md'):
    """Only the bounded result summary is generated; methods/docs stay authored."""
    path = Path(path)
    start, end = '<!-- benchmark-results:start -->', '<!-- benchmark-results:end -->'
    if not path.exists():
        return
    text = path.read_text()
    if text.count(start) != 1 or text.count(end) != 1:
        return
    a, b = text.index(start) + len(start), text.index(end)
    if b < a:
        raise ValueError('Invalid README result boundaries')
    cells = report_views.aggregate_cells(data)
    items = report_views.aggregate_stats(cells, data['models'])
    graded = sum(item['graded_attempts'] for item in items)
    passed = sum(item['passed_cells'] for item in items)
    date = data['data_as_of'] or 'unavailable'
    lines = [f'**{graded:,}/{len(cells):,} model × task cells have valid saved grades; '
             f'{passed:,} have full marks.** '
             f'The other {graded - passed:,} graded cells are below full marks; '
             f'{len(cells) - graded:,} cells are ungraded.', '',
             'Saved data as of **' + date + '**; source snapshot `' + data['source_report_build_id'][:12] + '`. '
             'These are saved measurements, not live queue status.', '',
             '| Model | Client route | Graded / planned | Full marks | Below full marks | Ungraded | Mean correctness |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for item in items:
        score = 'Unmeasured' if item['score'] is None else f'{item["score"]:.1f}%'
        lines.append(f'| {item["model_label"]} | {route_label(item["model"], data["models"][item["model"]])} '
                     f'| {item["graded_attempts"]}/{item["planned_cells"]} | {item["passed_cells"]} '
                     f'| {item["graded_attempts"] - item["passed_cells"]} '
                     f'| {item["planned_cells"] - item["graded_attempts"]} | {score} |')
    lines += ['', 'Mean correctness gives each graded task equal weight: '
              '`mean(passed checks / total checks) × 100`. '
              'Full marks require all checks for that task to pass. '
              '**Coverage, task mix and recorded conditions differ; this is not a matched-coverage ranking.**', '',
              '| Capability category | Tasks per model | Graded / planned cells |',
              '| --- | ---: | ---: |']
    for category, label, _ in report_views.CAPABILITY_CATEGORIES:
        selected = [cell for cell in cells if cell['category'] == category]
        if not selected:
            continue
        tasks = len({cell['key'][1:] for cell in selected})
        n = sum(cell['grade'] is not None for cell in selected)
        lines.append(f'| {label} | {tasks} | {n}/{len(selected)} |')
    lines += ['', 'Valid cached grades count once per model and frozen task, including reusable earlier results. '
              'Original answers, grades and runtime identities are preserved. '
              'Ungraded states are not correctness zeros.', '',
              '[Full results and capability radar](https://manageryzy.github.io/rea-benchmark/) · '
              '[Native comparison](https://manageryzy.github.io/rea-benchmark/comparison.html) · '
              '[Localization, reconstruction and type/ABI results](https://manageryzy.github.io/rea-benchmark/capabilities.html)', '',
              '[Tokens and cost estimates](https://manageryzy.github.io/rea-benchmark/costs.html) '
              'use saved usage and frozen price snapshots. Missing usage leaves the total unknown. '
              'Codex estimates are API equivalents; subscription debit is unknown.']
    path.write_text(text[:a] + '\n\n' + '\n'.join(lines) + '\n\n' + text[b:])


def stage_pages(site=OUTPUT, destination=ROOT / 'docs'):
    """Copy validated local HTML into the branch-based Pages publishing tree."""
    site, destination = Path(site).resolve(), Path(destination).resolve()
    validate_site(site)
    if site.is_relative_to(destination) or destination.is_relative_to(site):
        raise ValueError('Pages source and destination must be separate')
    if destination.exists() and any(destination.iterdir()):
        validate_site(destination)  # Refuse to overwrite guides or other data.
    with tempfile.TemporaryDirectory(prefix='.pages-stage-', dir=destination.parent) as temporary:
        stage = Path(temporary) / 'site'
        shutil.copytree(site, stage)
        validate_site(stage)
        if destination.exists():
            destination.rename(Path(temporary) / 'previous')
        stage.rename(destination)
    print('LOCAL PAGES STAGED:', destination, '· static HTML only', flush=True)
