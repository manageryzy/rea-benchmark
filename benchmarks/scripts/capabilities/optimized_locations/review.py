"""Bounded byte-level review of the authored GCC optimized examples.

DWARF establishes source-instance provenance only. The arithmetic and control
flow below are checked separately against final executable bytes. This is a
coordinating implementation review, not independent task-validity acceptance.
Only these exact AMD64 instruction sequences and supplied calling premises are
recognized; other compiler realizations require their own review.
"""
import hashlib
import re
import struct

from capabilities import elf, locations
from . import dwarf, reference


def arithmetic(salt=reference.SALT, shift=7, *, cold=False):
    """Exact reviewed instruction encodings, including the actual return.

    Normal: movabs salt,rax; xor rax,rdi; rol shift,rdi; mov rdi,rax;
    shl 5,rax; lea 109(rdi,rax),rax; ret.
    Cold: movabs salt,rdx; mov rdi,rax; xor rdx,rax; rol shift,rax;
    imul 33,rax,rax; add 109,rax; ret.
    Each implements (rotl64(input XOR salt,shift)*33+109) modulo 2**64.
    """
    if type(salt) is not int or not 0 <= salt < 2**64 or type(shift) is not int or not 1 <= shift < 64:
        raise ValueError('Unsupported arithmetic review premise')
    if cold:
        return (b'\x48\xba' + struct.pack('<Q', salt) + bytes.fromhex('4889f84831d048c1c0')
                + bytes([shift]) + bytes.fromhex('486bc0214883c06dc3'))
    return (b'\x48\xb8' + struct.pack('<Q', salt) + bytes.fromhex('4831c748c1c7')
            + bytes([shift]) + bytes.fromhex('4889f848c1e005488d44076dc3'))


def branch(raw, start):
    """Resolve only unconditional short/near tail transfers."""
    if len(raw) == 2 and raw[0] == 0xeb:
        return start + 2 + int.from_bytes(raw[1:], 'little', signed=True)
    if len(raw) == 5 and raw[0] == 0xe9:
        return start + 5 + int.from_bytes(raw[1:], 'little', signed=True)
    raise ValueError('Expected a reviewed direct tail transfer')


def inspect(target, mode):
    if mode not in ('optimized', 'lto'):
        raise ValueError('Unknown optimized-location review condition')
    image, public = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
    symbols = image.symbols()
    decoded = locations.instructions(target['disassembly'], image)
    ends = {start + row['size'] for start, row in decoded.items()}
    reader = dwarf.Locations(target)
    candidates = reader.candidates()

    def symbol(name):
        value = symbols.get(name)
        if value is None or value['type'] != 2 or not 1 <= value['size'] <= 256:
            raise ValueError('Missing bounded optimized function: ' + name)
        return value

    def raw(name):
        value = symbol(name)
        return image.bytes_at(value['rva'], value['size'])

    def fragment(start, size):
        if start not in decoded or start + size not in ends:
            raise ValueError('Reviewed range cuts an instruction boundary')
        cursor = start
        while cursor < start + size:
            if cursor not in decoded:
                raise ValueError('Reviewed range has an undecoded instruction')
            cursor += decoded[cursor]['size']
        if cursor != start + size:
            raise ValueError('Reviewed range ends inside an instruction')
        data = image.bytes_at(start, size)
        return {'start': start, 'end': start + size, 'bytes_hex': data.hex(),
                'sha256': hashlib.sha256(data).hexdigest()}

    def whole(name):
        value = symbol(name)
        return fragment(value['rva'], value['size'])

    def require_body(name, expected):
        if raw(name) != expected:
            raise ValueError('Final arithmetic/adapter bytes differ from review: ' + name)
        return whole(name)

    def origin(name, owner, start, end, *, inlined=True):
        matches = [row for row in candidates if row['name'] == name and row['owner'] == owner
                   and row['inlined'] is inlined and row['ranges'] == [{'start': start, 'end': end}]]
        if len(matches) != 1:
            raise ValueError('Missing unambiguous same-build source-instance provenance')
        return matches[0]['die']

    groups = {'inline': [], 'clone': [], 'cold': [], 'thunk': []}
    for name in ('operation_inline_a', 'operation_inline_b'):
        part = require_body(name, arithmetic())
        die = origin('mix_word', name, part['start'], part['end'] - 1)
        groups['inline'].append({'ranges': [part], 'origin_die': die,
                                 'calling_premise': 'u64_sysv_argument_0'})

    clone_names = [name for name in symbols if name.startswith('_ZL15clone_candidate') and '.constprop.' in name]
    good = [name for name in clone_names if raw(name) == arithmetic()]
    near = [name for name in clone_names if raw(name) == arithmetic(reference.SALT ^ 0x10001)]
    if len(clone_names) != 2 or len(good) != 1 or len(near) != 1:
        raise ValueError('Expected two actual specializations with distinct semantics')
    part = whole(good[0])
    die = origin('mix_word', 'clone_candidate', part['start'], part['end'] - 1)
    roots = []
    for name in ('operation_clone_a', 'operation_clone_b'):
        value = symbol(name)
        if branch(raw(name), value['rva']) != part['start']:
            raise ValueError('Clone wrapper does not reach the reviewed specialization')
        roots.append(whole(name))
    if branch(raw('distractor_clone'), symbol('distractor_clone')['rva']) != symbol(near[0])['rva']:
        raise ValueError('Near-miss wrapper has different actual specialization provenance')
    groups['clone'].append({'ranges': [part], 'origin_die': die, 'entry_paths': roots,
                            'calling_premise': 'u64_sysv_argument_0'})

    hot, cold, helper = symbol('operation_cold'), symbol('operation_cold.cold'), symbol('_ZL10cold_mixerm')
    hot_bytes = raw('operation_cold')
    # cmp input,-31; je cold. All other inputs execute the normal exact body.
    if (len(hot_bytes) != 10 + len(arithmetic()) or hot_bytes[:6] != bytes.fromhex('4883ffe10f84')
            or hot_bytes[10:] != arithmetic()
            or hot['rva'] + 10 + int.from_bytes(hot_bytes[6:10], 'little', signed=True) != cold['rva']):
        raise ValueError('Cold partition has unreviewed condition, destination or hot arithmetic')
    cold_bytes = raw('operation_cold.cold')
    # The taken path restores the exact compared input before tail-calling.
    if cold_bytes[:7] != bytes.fromhex('48c7c7e1ffffff') or branch(cold_bytes[7:], cold['rva'] + 7) != helper['rva']:
        raise ValueError('Cold fragment does not preserve its branch input')
    require_body('_ZL10cold_mixerm', arithmetic(cold=True))
    expected_ranges = sorted([{'start': v['rva'], 'end': v['rva'] + v['size']} for v in (hot, cold)],
                             key=lambda row: row['start'])
    origins = [row for row in candidates if row['name'] == 'operation_cold' and not row['inlined']
               and row['ranges'] == expected_ranges]
    if len(origins) != 1:
        raise ValueError('Cold/hot ranges do not share a concrete source function')
    groups['cold'].append({'ranges': [whole(name) for name in
                                      ('operation_cold', 'operation_cold.cold', '_ZL10cold_mixerm')],
                            'origin_die': origins[0]['die'], 'calling_premise': 'u64_sysv_argument_0'})

    method, thunk = '_ZN5Mixer5applyEm', '_ZThn16_N5Mixer5applyEm'
    # xor owner.cookie@8,rsi; rol 7,rsi; copy/shift/LEA as above; ret.
    method_part = require_body(method, bytes.fromhex('4833770848c1c6074889f048c1e005488d44066dc3'))
    thunk_bytes = raw(thunk)
    if thunk_bytes[:4] != bytes.fromhex('4883ef10') or branch(thunk_bytes[4:], symbol(thunk)['rva'] + 4) != method_part['start']:
        raise ValueError('This-adjusting thunk does not reach the reviewed method')
    interface, wrapper = '_ZL14call_interfaceP10ActionBasem', 'operation_object'
    interface_value, wrapper_value = symbol(interface), symbol(wrapper)
    vtable = symbols.get('_ZTV5Mixer')
    if vtable is None or vtable['type'] != 1 or vtable['size'] != 56:
        raise ValueError('Receiver context lacks its actual initialized vtable')
    table = struct.unpack('<7Q', image.bytes_at(vtable['rva'], 56))
    if (table[0] != 0 or table[1] != 0 or table[3] != symbol(method)['address']
            or table[4] != 2**64 - 16 or table[5] != 0 or table[6] != symbol(thunk)['address']):
        raise ValueError('Receiver context vtable does not reach the reviewed thunk')
    wrapper_expected = (bytes.fromhex('4883ec284889fe64488b042528000000488944241831c0488d7c2410')
        + bytes.fromhex('48c70424') + struct.pack('<I', vtable['address'] + 16)
        + b'\x48\xb8' + struct.pack('<Q', reference.SALT)
        + bytes.fromhex('48c7442410') + struct.pack('<I', vtable['address'] + 48)
        + bytes.fromhex('4889442408e8')
        + struct.pack('<i', interface_value['address'] - wrapper_value['address'] - 65)
        + bytes.fromhex('488b54241864482b14252800000075054883c428c3'))
    wrapper_bytes = raw(wrapper)
    if (len(wrapper_bytes) != 91 or wrapper_bytes[:86] != wrapper_expected or wrapper_bytes[86] != 0xe8
            or re.fullmatch(r'call\s+[0-9a-f]+\s+<__stack_chk_fail@plt>', decoded[wrapper_value['rva'] + 86]['assembly']) is None):
        raise ValueError('Receiver wrapper context differs from the reviewed initialization/call path')
    alternatives = []
    interface_bytes = raw(interface)
    if mode == 'optimized':
        if interface_bytes != bytes.fromhex('488b07ff20'):
            raise ValueError('Receiver interface context has an unreviewed dispatch')
    else:
        expected_head = bytes.fromhex('488b07488b00483d') + struct.pack('<I', symbol(thunk)['address'])
        if (len(interface_bytes) != 26 or interface_bytes[:12] != expected_head
                or interface_bytes[12:18] != bytes.fromhex('750a4883ef10')
                or branch(interface_bytes[18:20], interface_value['rva'] + 18) != method_part['start']
                or interface_bytes[20:] != bytes.fromhex('0f1f4000ffe0')):
            raise ValueError('LTO receiver fast path has an unreviewed condition or adjustment')
        # The checked initialized vtable makes this guard true. The inlined
        # adjustment and transfer are an actual alternative to the stand-alone
        # thunk, sharing the same arithmetic implementation.
        alternatives.append([fragment(interface_value['rva'] + 14, 6), method_part])
    groups['thunk'].append({'ranges': [whole(thunk), method_part], 'this_adjustment': -16,
                            'alternative_ranges': alternatives,
                            'entry_paths': [whole(wrapper), whole(interface)],
                            'calling_premise': 'program_initialized_receiver; owner_cookie_at_8_equals_salt; value_in_rsi'})

    if mode == 'lto':
        if 'cross_unit_mix' in symbols:
            raise ValueError('LTO example retained the source cross-unit boundary')
        part = require_body('operation_lto', arithmetic())
        die = origin('cross_unit_mix', 'operation_lto', part['start'], part['end'] - 1)
        groups['lto'] = [{'ranges': [part], 'origin_die': die, 'calling_premise': 'u64_sysv_argument_0'}]
    else:
        part = require_body('cross_unit_mix', arithmetic())
        root = symbol('operation_lto')
        if branch(raw('operation_lto'), root['rva']) != part['start']:
            raise ValueError('Non-LTO cross-unit call boundary is missing')
        groups['cross_unit_boundary'] = [{'ranges': [part], 'entry_paths': [whole('operation_lto')],
                                         'calling_premise': 'u64_sysv_argument_0'}]
    require_body('distractor', arithmetic(shift=9))
    return {'schema_version': 1, 'binary_sha256': hashlib.sha256(public.raw).hexdigest(),
            'review': 'coordinating_exact_instruction_review_v1', 'independent_review': False,
            'groups': groups, 'near_miss_ranges': [whole(near[0]), whole('distractor')],
            'dwarf_versions': sorted({unit[2] for unit in reader.units}),
            'instruction_starts': sorted(decoded),
            'release_accepted': False}
