"""Task-scoped tagged-object descriptions; no submitted code or type names."""
from grading.artifacts import object_fields, integer, digest, sequence, unique


def admit(value):
    object_fields(value, ('schema_version', 'binary_sha256', 'size', 'alignment',
                          'tag_offset', 'subobjects', 'payload'))
    integer(value['schema_version'], 1, 1)
    digest(value['binary_sha256'])
    integer(value['size'], 1, 128)
    if type(value['alignment']) is not int or value['alignment'] not in (1, 2, 4, 8, 16):
        raise ValueError('Unsupported tagged-object alignment')
    if value['size'] % value['alignment']:
        raise ValueError('Object extent contradicts alignment')
    regions = []
    def region(offset, width):
        integer(offset, 0, 127)
        if offset + width > value['size'] or any(offset < b and a < offset + width for a, b in regions):
            raise ValueError('Overlapping or out-of-object role')
        regions.append((offset, offset + width))
    region(value['tag_offset'], 4)
    roles = []
    for subobject in sequence(value['subobjects'], 2):
        object_fields(subobject, ('role', 'offset'))
        role = subobject['role']
        if not isinstance(role, str) or role not in ('cookie', 'state'):
            raise ValueError('Unknown semantic subobject role')
        region(subobject['offset'], 8)
        roles.append(role)
    unique(roles)
    if set(roles) != {'cookie', 'state'}:
        raise ValueError('Both supplied uint64 subobject roles are required')
    payload = value['payload']
    object_fields(payload, ('offset', 'extent', 'alternatives'))
    integer(payload['extent'], 8, 32)
    region(payload['offset'], payload['extent'])
    alternatives, tags = [], []
    for alternative in sequence(payload['alternatives'], 2):
        if not isinstance(alternative, dict) or not isinstance(alternative.get('role'), str):
            raise ValueError('Missing active alternative role')
        role = alternative['role']
        if role == 'number':
            object_fields(alternative, ('role', 'tag', 'offset'))
            cells = [(alternative['offset'], 8)]
        elif role == 'pair':
            object_fields(alternative, ('role', 'tag', 'first_offset', 'second_offset'))
            cells = [(alternative['first_offset'], 4), (alternative['second_offset'], 4)]
        else:
            raise ValueError('Unknown tagged payload alternative')
        integer(alternative['tag'], 0, 2**32 - 1)
        occupied = []
        for offset, width in cells:
            integer(offset, 0, 31)
            if offset + width > payload['extent'] or any(offset < b and a < offset + width for a, b in occupied):
                raise ValueError('Overlapping or out-of-payload alternative fields')
            occupied.append((offset, offset + width))
        tags.append(alternative['tag'])
        alternatives.append(role)
    unique(tags); unique(alternatives)
    if set(alternatives) != {'number', 'pair'}:
        raise ValueError('Both supplied payload alternatives are required')
    return value
