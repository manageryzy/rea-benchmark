"""Pure typed score vectors and family/lineage aggregation shared by registration and reports."""
from collections import defaultdict
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
POLICY = 'benchmarks/config/capability-measurement-policy.json'


def policy(read_bytes=Path.read_bytes, *, root=ROOT):
    value = json.loads(read_bytes(Path(root) / POLICY))
    expected = {'schema_version': 1, 'id': 'typed-family-macros-v1',
        'metrics': {'L1': 'top1_success', 'L2_L3_L4': 'f1_with_precision_and_coverage',
                    'L5': 'full_chain_success_with_components', 'C_T': 'capability_check_fraction'},
        'weights': {'variants': 'equal_within_family', 'families': 'equal_within_lineage',
                    'lineages': 'equal', 'conditions': 'separate'},
        'lineage_rules': {'abi_probe': 'integer_record_interfaces_v1', 'default': 'family_id'},
        'readability': {'rubric': ['names', 'decomposition', 'language_idioms', 'formatting', 'explanatory_comments'],
                        'points_per_dimension': 2, 'structured_artifacts': 'not_applicable'},
        'comparison': {'matched': 'identical_public_packages_all_selected_models',
                       'missing': 'unscored', 'infrastructure_invalid': 'unscored'}}
    if value.get('id') == 'typed-family-macros-v2':
        expected['id'] = 'typed-family-macros-v2'
        expected['metrics']['L6'] = 'instance_f1_with_precision_and_coverage'
        expected['lineage_rules'].update({
            'pointer_probe': 'pointer_object_interfaces_v1', 'variant_object': 'variant_object_interfaces_v1',
            'indirect_call': 'indirect_call_interfaces_v1', 'optimized_localization': 'optimized_mix_locations_v1',
            'native_abi': 'native_aggregate_interfaces_v1'})
    if value != expected:
        raise ValueError('Unsupported measurement policy; implement and validate a new explicit revision')
    return value


def lineage(kind, family):
    return {'abi_probe': 'integer_record_interfaces_v1', 'pointer_probe': 'pointer_object_interfaces_v1',
            'variant_object': 'variant_object_interfaces_v1', 'indirect_call': 'indirect_call_interfaces_v1',
            'optimized_localization': 'optimized_mix_locations_v1',
            'native_abi': 'native_aggregate_interfaces_v1'}.get(kind, family)

def fraction(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('Invalid typed score')
    return float(value)

def counts(value):
    passed, total = value['passed'], value['total']
    if type(passed) is not int or type(total) is not int or not 0 <= passed <= total <= 10**7:
        raise ValueError('Invalid typed check counts')
    return passed, total

def score_vector(grade, capability_ids):
    """Use the existing grader metrics; never average unlike capabilities."""
    result = {}
    for capability in capability_ids:
        item = grade.get('capabilities', {}).get(capability)
        components = {}
        if not grade['artifact_valid']:
            score = 0.0
        elif item is None:
            # Absent-target L tasks have no semantic-site/chain observations.
            # A compilation failure, in contrast, is a failed C submission.
            score = 0.0 if grade.get('compiled') is False else None
        elif capability == 'L1':
            if type(item['top1']) is not bool:
                raise ValueError('Invalid localization root result')
            score = float(item['top1'])
        elif capability in ('L2', 'L3', 'L4', 'L6'):
            score = fraction(item['f1'])
            components = {name: fraction(item.get(name)) for name in ('precision', 'coverage')}
        elif capability == 'L5':
            if type(item['root_correct']) is not bool:
                raise ValueError('Invalid chain root result')
            components = {'root_correct': float(item['root_correct']),
                          'nodes_f1': fraction(item['nodes']['f1']), 'edges_f1': fraction(item['edges']['f1'])}
            score = float(item['root_correct'] and components['nodes_f1'] == components['edges_f1'] == 1)
        else:
            passed, total = counts(item)
            score = passed / total if total else None
        result[capability] = {'score': score, 'components': components}
    return result

def macro_vectors(cells, models):
    groups = defaultdict(list)
    for cell in cells:
        if cell['scheduled']:
            for capability in cell['capability_ids']:
                groups[(cell['model'], cell['profile_id'], capability)].append(cell)
    conditions = defaultdict(set)
    for (_, profile, capability), rows in groups.items():
        conditions[(profile, capability)].update(row.get('analysis_condition', 'paged-evidence-v2') for row in rows)
    if any(len(values) > 1 for values in conditions.values()):
        raise ValueError('Different evidence-access conditions require separate capability macros')
    common = {}
    for _, profile, capability in groups:
        key = profile, capability
        if key in common:
            continue
        populations = []
        for model in models:
            populations.append({(row['family_id'], row['public_package_sha256'])
                for row in groups.get((model, profile, capability), [])
                if row['observation'] and row['observation']['grade']
                and row['observation']['grade']['scores'].get(capability, {}).get('score') is not None})
        common[key] = set.intersection(*populations) if populations else set()
    output = []
    for (model, profile, capability), rows in sorted(groups.items()):
        families, lineages = defaultdict(list), {}
        observed = 0
        for row in rows:
            family = row['family_id']
            lineages[family] = row['lineage_id']
            grade = row['observation']['grade'] if row['observation'] else None
            score = grade['scores'].get(capability, {}).get('score') if grade else None
            if score is not None:
                families[family].append(score)
                observed += 1
        family_means = {name: sum(values) / len(values) for name, values in families.items()}
        lineage_means = defaultdict(list)
        for name, mean in family_means.items():
            lineage_means[lineages[name]].append(mean)
        means = [sum(values) / len(values) for values in lineage_means.values()]
        matched_families = defaultdict(list)
        for row in rows:
            if (row['family_id'], row['public_package_sha256']) in common[profile, capability]:
                matched_families[row['family_id']].append(row['observation']['grade']['scores'][capability]['score'])
        matched_lineages = defaultdict(list)
        for family, values in matched_families.items():
            matched_lineages[lineages[family]].append(sum(values) / len(values))
        matched = [sum(values) / len(values) for values in matched_lineages.values()]
        complete = all(row['observation'] and row['observation']['grade'] for row in rows)
        # A task with no applicable L metric is excluded from metric coverage,
        # but still has to complete before a full-denominator mean is shown.
        output.append({'model': model, 'profile_id': profile, 'capability_id': capability,
            'scheduled_cells': len(rows), 'graded_cells': sum(bool(row['observation'] and row['observation']['grade']) for row in rows),
            'metric_observations': observed, 'scheduled_families': len({row['family_id'] for row in rows}),
            'observed_families': len(family_means), 'observed_lineages': len(means),
            'observed_macro': sum(means) / len(means) if means else None,
            'complete_macro': sum(means) / len(means) if means and complete else None,
            'matched_cells': len(common[profile, capability]), 'matched_families': len(matched_families),
            'matched_macro': sum(matched) / len(matched) if matched else None})
    return output
