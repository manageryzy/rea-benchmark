"""Development controls for observable T4 roles; no task release or enrollment."""

VARIANTS = ('baseline', 'reordered')
NONCES = tuple(range(12)) + (0x8000000000000000, 0xffffffff00000004,
                             0xfffffffffffffff5, 0xffffffffffffffff)
WIDTH = 64


def gold(binary_sha256, variant='baseline'):
    if variant not in VARIANTS:
        raise ValueError('Unknown tagged-object layout')
    baseline = variant == 'baseline'
    return {'schema_version': 1, 'binary_sha256': binary_sha256, 'size': 32, 'alignment': 8,
        'tag_offset': 16 if baseline else 28,
        'subobjects': [{'role': 'cookie', 'offset': 0 if baseline else 8},
                       {'role': 'state', 'offset': 8 if baseline else 0}],
        'payload': {'offset': 24 if baseline else 16, 'extent': 8, 'alternatives': [
            {'role': 'number', 'tag': 0x31 if baseline else 0xa7, 'offset': 0},
            {'role': 'pair', 'tag': 0x85 if baseline else 0x42,
             'first_offset': 0 if baseline else 4, 'second_offset': 4 if baseline else 0}]}}
