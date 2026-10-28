"""Bounded L1–L5 artifact grading against private same-build locations.

No symbol search or original source is used while grading a submission. The
builder must validate the truth against the stripped binary before publishing
it. Addresses are RVAs from the lowest PT_LOAD page, for PIE and ET_EXEC alike.
"""
from . import artifacts


def _set_score(submitted, expected):
    submitted, expected = set(submitted), set(expected)
    correct = len(submitted & expected)
    # Reporting extra nodes/edges/objects lowers precision. Empty expectations
    # carry no score; unsupported facts cannot earn free correctness.
    return {'correct': correct, 'expected': len(expected), 'submitted': len(submitted),
            'precision': correct / len(submitted) if submitted else 0.0,
            'coverage': correct / len(expected) if expected else None,
            'f1': 2 * correct / (len(submitted) + len(expected)) if expected else None}


def grade(submission, truth):
    artifacts.locations(submission)
    if submission['binary_sha256'] != truth['binary_sha256']:
        raise ValueError('Location artifact targets a different binary')
    if (truth.get('schema_version') != 1 or truth.get('validated') is not True
            or truth.get('semantic_policy') != 'required-sites-context-v1'):
        raise ValueError('Localization requires validated private truth')
    present = truth['present']
    if type(present) is not bool:
        raise ValueError('Invalid private target status')
    status = submission['status']
    result = {'schema_version': 1, 'artifact_valid': True, 'target_present': present,
              'answered': status != 'unanswered', 'capabilities': {}}
    result['capabilities']['L1'] = {
        'top1': (status == 'present' and bool(submission['roots']) and submission['roots'][0] in truth['roots'])
                if present else status == 'absent',
        'alternative_hit': present and status == 'present' and any(root in truth['roots'] for root in submission['roots'][1:]),
        'presence_correct': status == ('present' if present else 'absent')}
    if not present:
        return result
    starts = set(truth['instruction_starts'])
    # Required operations determine coverage. Reviewed optional context can
    # improve a range's description without being required for full coverage.
    # It never substitutes for a missing operation. Every other instruction,
    # role and invalid boundary remains an overclaim in the precision score.
    expected, context, submitted = set(), set(), set()
    for role, ranges in truth['roles'].items():
        expected.update((role, address) for address in starts
                        if any(region['start'] <= address < region['end'] for region in ranges))
    if set(truth.get('role_context', {})) != set(truth['roles']):
        raise ValueError('Localization requires reviewed semantic context')
    for role, ranges in truth['role_context'].items():
        context.update((role, address) for address in starts
                       if any(region['start'] <= address < region['end'] for region in ranges))
    if not expected or expected & context:
        raise ValueError('Invalid required/context semantic truth')
    for site in submission['sites']:
        submitted.update((site['role'], address) for address in range(site['start'], site['end'])
                         if address in starts or not any(segment['kind'] == 'code' and segment['start'] <= address < segment['end']
                                                         for segment in truth['segments']))
        if site['start'] not in starts:
            submitted.add((site['role'], site['start']))
        if site['end'] not in truth['instruction_ends']:
            submitted.add((site['role'], site['end'], 'partial_instruction'))
    coverage = len(submitted & expected) / len(expected)
    precision = len(submitted & (expected | context)) / len(submitted) if submitted else 0.0
    result['capabilities']['L2'] = {
        'correct': len(submitted & expected), 'expected': len(expected), 'submitted': len(submitted),
        'accepted': len(submitted & (expected | context)), 'precision': precision, 'coverage': coverage,
        'f1': 2 * precision * coverage / (precision + coverage) if precision + coverage else 0.0,
        'policy': 'required-sites-context-v1'}
    keys = ('condition', 'site', 'target')
    result['capabilities']['L3'] = _set_score(
        [tuple(edge[key] for key in keys) for edge in submission['dispatch']],
        [tuple(edge[key] for key in keys) for edge in truth['dispatch']])
    keys = ('site', 'object', 'length', 'access')
    result['capabilities']['L4'] = _set_score(
        [tuple(edge[key] for key in keys) for edge in submission['references']],
        [tuple(edge[key] for key in keys) for edge in truth['references']])
    chain, expected_chain = submission['chain'], truth['chain']
    nodes = _set_score(chain['nodes'] if chain else [], expected_chain['nodes'])
    edges = _set_score([tuple(edge) for edge in chain['edges']] if chain else [],
                       [tuple(edge) for edge in expected_chain['edges']])
    result['capabilities']['L5'] = {'root_correct': bool(chain) and chain['root'] in truth['roots'],
                                   'nodes': nodes, 'edges': edges}
    return result
