"""Private type facts and public semantic roles for three existing program families.

Original declarations live in authored C++ headers. These facts describe them;
they are never a substitute for executing frozen original object code.
"""
import copy

FAMILIES = ('record_codec', 'numeric_aggregation', 'variable_objects')
CONVENTIONS = ('sysv_amd64', 'ms_x64')
CONDITIONS = {'scalar_recover': 'T1', 'layout_recover': 'T2', 'abi_recover': 'T5'}
NONCES = (0, 1, 0x7fff, 0x8000, 0xffff, 0x80000000, 0xffffffff,
          0x8000000000000000, 0xffffffffffffffff, 0x123456789abcdef0,
          0xa71f80ff0d0a0001, 0x6d350000ffff8000)


def integer(name, bits, signed=False):
    return {'id': name, 'kind': 'integer', 'bits': bits, 'signed': signed}


def field(role, offset, type_id):
    return {'role': role, 'offset': offset, 'type': type_id}


def record(name, size, alignment, fields):
    return {'id': name, 'kind': 'record', 'size': size, 'alignment': alignment, 'fields': fields}


def gold(family, binary_sha256):
    scalars = [integer('signed16', 16, True), integer('unsigned16', 16), integer('unsigned32', 32), integer('byte', 8)]
    if family == 'record_codec':
        types = scalars + [
            {'id': 'values', 'kind': 'array', 'element': 'unsigned32', 'count': 3, 'stride': 4, 'alignment': 4},
            record('root', 24, 4, [field('delta', 0, 'signed16'), field('weight', 4, 'unsigned32'),
                field('flags', 8, 'byte'), field('values', 12, 'values')])]
    elif family == 'numeric_aggregation':
        types = scalars + [record('root', 8, 4, [field('delta', 0, 'signed16'), field('weight', 4, 'unsigned32')])]
    elif family == 'variable_objects':
        types = scalars + [record('item', 4, 2, [field('key', 0, 'unsigned16'), field('change', 2, 'signed16')]),
            {'id': 'items', 'kind': 'array', 'element': 'item', 'count': 2, 'stride': 4, 'alignment': 2},
            record('root', 16, 4, [field('kind', 0, 'byte'), field('items', 2, 'items'), field('length', 12, 'unsigned32')])]
    else:
        raise ValueError('Unknown type-probe family')
    return {'schema_version': 1, 'binary_sha256': binary_sha256, 'root': 'root', 'types': copy.deepcopy(types)}


def paths(family):
    if family == 'record_codec':
        return [('delta',), ('weight',), ('flags',), *[('values', index) for index in range(3)]]
    if family == 'numeric_aggregation':
        return [('delta',), ('weight',)]
    if family == 'variable_objects':
        return [('kind',), ('items', 0, 'key'), ('items', 0, 'change'),
                ('items', 1, 'key'), ('items', 1, 'change'), ('length',)]
    raise ValueError('Unknown type-probe family')


def abi_claim(family, convention, binary_sha256, entry_rva):
    if convention not in CONVENTIONS:
        raise ValueError('Unsupported ABI convention')
    size = {'record_codec': 24, 'numeric_aggregation': 8, 'variable_objects': 16}[family]
    reg = lambda name, size=8: {'kind': 'register', 'name': name, 'size': size}
    if convention == 'sysv_amd64':
        indirect = size > 16
        record_locations = [{'kind': 'stack', 'offset': 8, 'size': size}] if indirect else [reg(name) for name in ('rdi', 'rsi')[:(size + 7) // 8]]
        nonce = 'rsi' if indirect else ('rsi' if size <= 8 else 'rdx')
        hidden = 'rdi' if indirect else None
        result_locations = [reg('rax')] if indirect else [reg(name) for name in ('rax', 'rdx')[:(size + 7) // 8]]
        preserved = ['rbx', 'rbp', 'rsp', 'r12', 'r13', 'r14', 'r15']
        passing = 'stack_value' if indirect else 'register_value'
    else:
        indirect = size not in (1, 2, 4, 8)
        record_locations = [reg('rdx' if indirect else 'rcx')]
        nonce, hidden = ('r8', 'rcx') if indirect else ('rdx', None)
        result_locations = [reg('rax')]
        preserved = ['rbx', 'rbp', 'rsp', 'rdi', 'rsi', 'r12', 'r13', 'r14', 'r15', *['xmm' + str(i) for i in range(6, 16)]]
        passing = 'indirect_value' if indirect else 'register_value'
    return {'schema_version': 1, 'binary_sha256': binary_sha256, 'entry_rva': entry_rva,
            'convention': convention, 'arguments': [
                {'role': 'record', 'passing': passing, 'locations': record_locations},
                {'role': 'nonce', 'passing': 'register_value', 'locations': [reg(nonce)]}],
            'return': {'passing': 'indirect_value' if indirect else 'register_value',
                       'hidden_pointer': hidden, 'locations': result_locations},
            'stack_alignment': 16, 'shadow_space': 32 if convention == 'ms_x64' else 0,
            'preserved': preserved}
