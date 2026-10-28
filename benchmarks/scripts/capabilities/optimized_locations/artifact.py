"""Bounded diagnostic location artifact, with no compiler-history labels."""
import re

from grading.artifacts import decode

KINDS = ('inline', 'clone', 'cold', 'thunk', 'lto', 'cross_unit_boundary')


def admit(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 131072:
        raise ValueError('Optimized location artifact exceeds its byte bound')
    value = decode(raw)
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'binary_sha256', 'instances'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['binary_sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', value['binary_sha256']) is None
            or not isinstance(value['instances'], list) or len(value['instances']) > 128):
        raise ValueError('Invalid optimized location artifact contract')
    seen = set()
    for instance in value['instances']:
        if (not isinstance(instance, dict) or set(instance) != {'ranges'}
                or not isinstance(instance['ranges'], list) or not 1 <= len(instance['ranges']) <= 16):
            raise ValueError('Invalid optimized instance')
        ranges = []
        for part in instance['ranges']:
            if (not isinstance(part, dict) or set(part) != {'start', 'end'}
                    or any(type(part[key]) is not int for key in ('start', 'end'))
                    or not 0 <= part['start'] < part['end'] < 2**48 or part['end'] - part['start'] > 4096):
                raise ValueError('Invalid optimized code range')
            ranges.append((part['start'], part['end']))
        ranges.sort()
        if any(left[1] > right[0] for left, right in zip(ranges, ranges[1:])):
            raise ValueError('Overlapping or repeated optimized ranges')
        key = tuple(ranges)
        if key in seen:
            raise ValueError('Duplicate optimized instance')
        seen.add(key)
    return value
