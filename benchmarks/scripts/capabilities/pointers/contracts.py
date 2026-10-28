"""Private controls for two layouts of the existing variable-object family."""
import copy

VARIANTS = ('baseline', 'reordered', 'indirect', 'indirect_reordered')
NONCES = tuple(range(12)) + (0xffffffff00000004, 0xfffffffffffffff5)
WIDTH = 128


def gold(binary_sha256, variant='baseline'):
    if variant not in VARIANTS:
        raise ValueError('Unknown pointer-record original')
    offsets = (dict(destination=0, capacity=8, produced=12, source=16, length=24)
               if variant in ('baseline', 'indirect') else
               dict(length=0, source=8, produced=16, capacity=20, destination=24))
    fields = []
    for role, offset in offsets.items():
        field = {'role': role, 'kind': 'pointer' if role in ('source', 'destination') else 'u32', 'offset': offset}
        if field['kind'] == 'pointer':
            field.update(indirections=2 if variant.startswith('indirect') else 1,
                         access='read' if role == 'source' else 'write',
                         extent='length' if role == 'source' else 'capacity')
        fields.append(field)
    return copy.deepcopy({'schema_version': 1, 'binary_sha256': binary_sha256,
                          'size': 32, 'alignment': 8, 'fields': fields, 'aliasing': 'may_overlap'})
