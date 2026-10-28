"""Private T6 controls, excluded from submitted-description compiler inputs."""
VARIANTS = ('baseline', 'reordered')
NONCES = tuple(range(24)) + (0x8000000000000000, 0xffffffff00000001,
                             0xfffffffffffffffe, 0xffffffffffffffff)
WIDTH = 64


def gold(binary_sha256, variant='baseline'):
    if variant not in VARIANTS:
        raise ValueError('Unknown indirect-call original')
    baseline = variant == 'baseline'
    return {'schema_version': 1, 'binary_sha256': binary_sha256, 'operations': [
        {'operation': i, 'receiver_slot': i if baseline else 1 - i,
         'callback_slot': 1 - i if baseline else i, 'context_slot': i if baseline else 1 - i,
         'virtual_slot': (1, 3)[i] if baseline else (2, 0)[i],
         'this_adjustment': -16 if baseline else -32} for i in range(2)]}
