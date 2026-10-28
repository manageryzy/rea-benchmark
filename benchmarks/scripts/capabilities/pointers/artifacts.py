"""Bounded T3 record descriptions. Admission is separate from correctness."""
from grading.artifacts import object_fields, integer, digest, sequence, unique

ROLES = {'source': 'pointer', 'destination': 'pointer', 'length': 'u32',
         'capacity': 'u32', 'produced': 'u32'}


def admit(value):
    object_fields(value, ('schema_version', 'binary_sha256', 'size', 'alignment',
                          'fields', 'aliasing'))
    integer(value['schema_version'], 1, 1)
    digest(value['binary_sha256'])
    integer(value['size'], 1, 128)
    if type(value['alignment']) is not int or value['alignment'] not in (1, 2, 4, 8, 16):
        raise ValueError('Unsupported pointer-record alignment')
    if value['size'] % value['alignment']:
        raise ValueError('Record size contradicts alignment')
    if not isinstance(value['aliasing'], str) or value['aliasing'] not in (
            'may_overlap', 'disjoint', 'identical_or_disjoint'):
        raise ValueError('Unknown buffer alias contract')
    roles, ranges = [], []
    for field in sequence(value['fields'], 5):
        if not isinstance(field, dict) or not isinstance(field.get('role'), str) or field['role'] not in ROLES:
            raise ValueError('Unknown pointer-object role')
        role = field['role']
        expected = ('role', 'kind', 'offset')
        if ROLES[role] == 'pointer':
            expected += ('indirections', 'access', 'extent')
        object_fields(field, expected)
        if field['kind'] != ROLES[role]:
            raise ValueError('Role contradicts the supplied scalar/pointer kind')
        integer(field['offset'], 0, 127)
        width = 8 if ROLES[role] == 'pointer' else 4
        end = field['offset'] + width
        if end > value['size'] or any(field['offset'] < b and a < end for a, b in ranges):
            raise ValueError('Overlapping or out-of-object fields')
        ranges.append((field['offset'], end))
        if width == 8:
            integer(field['indirections'], 1, 2)
            if not isinstance(field['access'], str) or field['access'] not in ('read', 'write', 'read_write'):
                raise ValueError('Unknown pointer access role')
            if not isinstance(field['extent'], str) or field['extent'] not in ('length', 'capacity', 'produced'):
                raise ValueError('Unknown buffer extent role')
        roles.append(role)
    unique(roles)
    if set(roles) != set(ROLES):
        raise ValueError('Every semantic object role must occur exactly once')
    return value
