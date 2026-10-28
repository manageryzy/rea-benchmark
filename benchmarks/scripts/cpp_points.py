"""Read saved compiler/optimization build coverage without invoking toolchains."""
import hashlib
import json

import records

ROOT, BENCH = records.ROOT, records.BENCH


def collect(data, read_bytes):
    config_path = BENCH / 'config/cpp-build-points.json'
    config = json.loads(read_bytes(config_path))
    validation_path = BENCH / 'config/cpp-build-validation.json'
    validation = json.loads(read_bytes(validation_path)) if validation_path.exists() else {'points': []}
    configuration_current = validation.get('config_sha256') == hashlib.sha256(read_bytes(config_path)).hexdigest()
    builder_current = validation.get('builder_sha256') == hashlib.sha256(read_bytes(BENCH / 'scripts/build_cpp_points.py')).hexdigest()
    rows = data['records'] + data.get('condition_records', [])
    for row in rows:
        if row['case'].get('native_case_origin') == 'compiler_matrix':
            row['target_build'] = {**row['case']['target_build'],
                                   'origin': 'Registered compiler variant; frozen target and prompt'}
            continue
        family = next((f for f in config['families'] if (f['experiment'], f['base_case']) == (row['experiment'], row['case']['id'])), None)
        if family:
            row['target_build'] = {'compiler_family': 'GCC', 'compiler_version': '13.3.0',
                'optimization': family['recorded_optimization'], 'standard': family['standard'],
                'string_abi': family['string_abi'], 'standard_library': config['standard_library'],
                'strip_mode': family['strip_mode'], 'extra_flags': family['extra_flags'],
                'origin': 'Existing frozen GCC fixtures; paths and run IDs preserved'}
    points = []
    for saved in validation['points']:
        point = {**saved}
        current = configuration_current and builder_current
        for name, expected in saved.get('source_sha256', {}).items():
            try:
                current = (hashlib.sha256(read_bytes(ROOT / name)).hexdigest() == expected) and current
            except OSError:
                current = False
        public = ROOT / saved['public_directory'] if saved.get('public_directory') else None
        if public:
            try:
                current = hashlib.sha256(read_bytes(public / 'target')).hexdigest() == saved['binary_sha256'] and current
                prompt_sha = hashlib.sha256(read_bytes(public / 'prompt.txt')).hexdigest() if (public / 'prompt.txt').exists() else None
            except OSError:
                current, prompt_sha = False, None
        else:
            prompt_sha = None
        current = current and bool(public and prompt_sha)
        point['inputs_current'] = current
        # Count measured conditions explicitly; build verification is never a model grade.
        point['measurements'] = [{'model': r['model'], 'condition': r['condition'], 'status': r['status'],
                                  'directory': r['directory'], 'recorded_case': r['case']['id'],
                                  'reuse_basis': 'Matching target bytes and task text in this experiment; recorded condition retained',
                                  'passed': r['grade']['passed'] if r['grade'] else None,
                                  'total': r['grade']['total'] if r['grade'] else None}
                                 for r in rows if current and prompt_sha and r['experiment'] == saved['experiment'] and r['receipt'] and
                                 r['receipt'].get('binary_sha256') == saved.get('binary_sha256') and
                                 r['receipt'].get('prompt_sha256') == prompt_sha]
        points.append(point)
    feature_path = BENCH / 'config/cpp-feature-validation.json'
    features = json.loads(read_bytes(feature_path)) if feature_path.exists() else {'probes': []}
    for probe in features['probes']:
        try:
            probe['inputs_current'] = hashlib.sha256(read_bytes(ROOT / probe['source'])).hexdigest() == probe['source_sha256'] and features['validator_sha256'] == hashlib.sha256(read_bytes(BENCH / 'scripts/probe_cpp_features.py')).hexdigest()
        except OSError:
            probe['inputs_current'] = False
    return {'schema_version': 1, 'config': config, 'build_validation': {k: v for k, v in validation.items() if k != 'points'},
            'planned_build_points': len(config['families']) * len(config['compilers']) * len(config['optimization_levels']),
            'points': points, 'language_feature_probes': features, 'model_api_calls_for_build_validation': 0,
            'note': 'Compiler/version, standard library, language standard, optimization, ABI and strip mode are distinct axes. New build points do not revise original model results or claim unmeasured accuracy.'}
