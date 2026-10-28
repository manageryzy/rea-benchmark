"""Fixture-local native-ABI calls using the existing instruction/memory guards.

This fixture-local loader maps trusted, frozen compiler outputs. It is a separate
profile from the freestanding C loader: runtime startup is never executed, only
the whitelisted original endpoints and generated probes. Guest returns and
defined observations are collected by the host, never from stdout.
"""
import hashlib
import struct

from unicorn import x86_const as x86
from grading.c_executor import machine as core
from grading import returns
from . import images

REGISTER = {name: getattr(x86, 'UC_X86_REG_' + name.upper()) for name in
            ('rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15')}
POISON = (0x8a91b6e237d04fc5, 0x2c40ef835b971da6, 0xd5731089c62a4bef)


def load_segments(image, limits):
    selected, pages, total, writable = [], set(), 0, 0
    for program in image.programs:
        kind, flags, offset, start, _, size, memory, alignment = program
        if kind != 1 or not memory:
            continue
        if (flags & ~7 or flags & 3 == 3 or not flags & 4 or size > memory
                or not 0x10000 <= start < 0x10000000 or memory > 0x10000000 - start
                or alignment > 1 and (alignment & (alignment - 1) or start % alignment != offset % alignment)):
            raise ValueError('Native ABI original has unsupported load mapping')
        data = core.elf.checked_slice(image.raw, offset, size)
        total += memory
        writable += memory if flags & 2 else 0
        region = set(range(start // 4096, (start + memory + 4095) // 4096))
        if (pages & region or total > limits['max_image_bytes']
                or writable > limits['max_image_writable_bytes']):
            raise ValueError('Native ABI image overlaps or exceeds its memory budget')
        pages.update(region)
        selected.append({'address': start, 'size': memory, 'data': data, 'flags': flags})
    if not selected:
        raise ValueError('Native ABI original has no bounded loadable image')
    return selected


def observe(original_raw, public_raw, probe_raw, request, limits):
    if (set(request) != {'language', 'direction', 'values', 'id'}
            or request['language'] not in ('rust', 'go')
            or request['direction'] not in ('reference', 'replacement', 'bridge')
            or type(request['id']) is not int or not 0 <= request['id'] < 10**12):
        raise ValueError('Invalid native ABI invocation contract')
    values = request['values']
    if (not isinstance(values, dict) or set(values) != {'nonce', 'first', 'second', 'third'}
            or any(type(v) is not int or not 0 <= v < 2**64 for v in values.values())):
        raise ValueError('Native ABI invocation accepts one argument vector only')
    language, direction = request['language'], request['direction']
    original, _ = images.validate(original_raw, public_raw, language)
    names = ('native_transform', 'native_caller', 'replacement_slot') if language == 'rust' else (
        'main.nativeTransform', 'main.nativeCaller', 'main.replacementSlot')
    endpoints = images.selected_symbols(original, names)
    callee, caller, slot = (endpoints[name] for name in names)
    for endpoint in (callee, caller):
        if endpoint['type'] != 2 or not 1 <= endpoint['size'] <= 4096:
            raise ValueError('Native ABI endpoint is not a bounded original function')
    if max(callee['address'], caller['address']) < min(callee['address'] + callee['size'], caller['address'] + caller['size']):
        raise ValueError('Native ABI original caller/callee boundaries overlap')
    segments = load_segments(original, limits)
    executable = [[v['address'], v['address'] + v['size']] for v in (callee, caller)]
    probe_symbols = {}
    if probe_raw is not None:
        probe = images.elf.ELF(probe_raw)
        # The standard strict freestanding loader independently admits the
        # generated probe before it is combined with the trusted original.
        core.elf.load(probe_raw, '__rea_native_replacement', limits)
        probe_symbols = images.selected_symbols(probe, ('__rea_native_replacement', '__rea_native_bridge'))
        additions = load_segments(probe, limits)
        for left in segments:
            for right in additions:
                if max(left['address'] // 4096, right['address'] // 4096) < min(
                        (left['address'] + left['size'] + 4095) // 4096,
                        (right['address'] + right['size'] + 4095) // 4096):
                    raise ValueError('Native ABI probe overlaps the original image')
        if sum(s['size'] for s in segments + additions) > limits['max_image_bytes']:
            raise ValueError('Combined native ABI image exceeds its budget')
        segments += additions
        executable += [[s['address'], s['address'] + s['size']] for s in additions if s['flags'] & 1]
    elif direction != 'reference':
        raise ValueError('Native ABI bridge/replacement requires a generated probe')
    entry = (probe_symbols['__rea_native_bridge']['address'] if direction == 'bridge'
             else caller['address'] if direction == 'replacement' else callee['address'])
    if direction == 'replacement' and language == 'rust':
        if slot['type'] != 2 or not 5 <= slot['size'] <= 4096 or any(
                max(slot['address'], endpoint['address']) < min(slot['address'] + slot['size'], endpoint['address'] + endpoint['size'])
                for endpoint in (callee, caller)):
            raise ValueError('Rust replacement slot is not separate original-object code')
        executable.append([slot['address'], slot['address'] + slot['size']])
    frame = struct.pack('<7Q', values['first'], values['second'], values['third'], values['nonce'], *POISON)
    dispatch = probe_symbols.get('__rea_native_replacement', {}).get('address', 0)
    invocation = {'id': request['id'], 'return_type': 'void', 'arguments': [], 'callbacks': [], 'regions': [
        {'id': 'frame', 'data_hex': frame.hex(), 'readable': [[0, 56]], 'writable': [[32, 24]]},
        {'id': 'go_thread', 'data_hex': bytes(64).hex(), 'readable': [[0, 64]], 'writable': []},
        {'id': 'dispatch', 'data_hex': struct.pack('<Q', dispatch).hex(), 'readable': [[0, 8]], 'writable': []}]}
    program_id = hashlib.sha256(original_raw + (probe_raw or b'') + (language + '/' + direction).encode()).hexdigest()

    def loader(raw, name, requested_limits):
        if raw != original_raw or name != 'native_fixture' or requested_limits != limits:
            raise ValueError('Native ABI loader invocation changed')
        return {'entry': entry, 'segments': segments, 'sha256': program_id}

    original_loader = core.elf.load
    core.elf.load = loader
    try:
        machine = core.Machine(original_raw, 'native_fixture', invocation, limits)
    finally:
        core.elf.load = original_loader
    engine = machine.engine
    frame_address = machine.regions['frame']['address']
    g_address = machine.regions['go_thread']['address']
    machine.executable = core.merge(executable)
    # Keep the root caller's return slot immutable. Go's supplied call context
    # reserves four uint64 spill slots above it; these are an argument area,
    # not a reason to make the entire caller stack writable.
    machine.writable = [[core.STACK, machine.stack_pointer], [frame_address + 32, frame_address + 56]]
    if language == 'go' and direction != 'bridge':
        spill = [machine.stack_pointer + 8, machine.stack_pointer + 40]
        machine.writable.append(spill)
        machine.readable = core.merge(machine.readable + [spill])
    engine.reg_write(REGISTER['r14'], g_address)
    machine.saved_registers[REGISTER['r14']] = g_address
    patch = None
    if direction == 'replacement':
        if language == 'rust':
            address = slot['address']
            data = b'\xe9' + struct.pack('<i', dispatch - address - 5)
        else:
            if slot['type'] != 1 or slot['size'] != 8:
                raise ValueError('Go replacement root is not a function-value pointer')
            address = slot['address']
            original_descriptor = int.from_bytes(original.bytes_at(slot['rva'], 8), 'little')
            if int.from_bytes(original.bytes_at(original_descriptor - original.base, 8), 'little') != callee['address']:
                raise ValueError('Go original descriptor does not target the retained native callee')
            data = struct.pack('<Q', machine.regions['dispatch']['address'])
        patch = {'address': address, 'before_hex': bytes(engine.mem_read(address, len(data))).hex(), 'after_hex': data.hex()}
        engine.mem_write(address, data)
    if direction == 'bridge':
        engine.reg_write(REGISTER['rdi'], frame_address)
    elif language == 'rust':
        for reg, value in (('rdi', frame_address + 32), ('rsi', frame_address), ('rdx', values['nonce'])):
            engine.reg_write(REGISTER[reg], value)
    else:
        for reg, value in zip(('rax', 'rbx', 'rcx', 'rdi'),
                              (values['first'], values['second'], values['third'], values['nonce'])):
            engine.reg_write(REGISTER[reg], value)
        # ABIInternal has no SysV RBX/R12 preservation promise. Its supplied
        # runtime-context register and the checked return stack remain guarded.
        machine.saved_registers = {REGISTER['r14']: g_address}
    preserved = {name: original.bytes_at(endpoint['rva'], endpoint['size'])
                 for name, endpoint in zip(('callee', 'caller'), (callee, caller))}
    for name, endpoint in zip(('callee', 'caller'), (callee, caller)):
        if bytes(engine.mem_read(endpoint['address'], endpoint['size'])) != preserved[name]:
            raise ValueError('Native ABI setup changed an original caller/callee')
    result = machine.run()
    result.pop('state', None)
    result['provenance'] = 'host_native_abi_return_v1'
    result['original_endpoints_preserved'] = all(
        bytes(engine.mem_read(endpoint['address'], endpoint['size'])) == preserved[name]
        for name, endpoint in zip(('callee', 'caller'), (callee, caller)))
    result['replacement_patch'] = patch
    if result['status'] == 'returned':
        data = (bytes(engine.mem_read(frame_address + 32, 24)) if language == 'rust' or direction == 'bridge'
                else struct.pack('<3Q', *(engine.reg_read(REGISTER[reg]) for reg in ('rax', 'rbx', 'rcx'))))
        result['values'] = list(struct.unpack('<3Q', data))
        result['return_record'] = {'invocation_id': request['id'], 'status': 'ok', 'value_kind': 'bytes',
            'declared_type': 'native_record_u64x3', 'length': 24, 'value_hex': data.hex(),
            'frame_hex': returns.encode(request['id'], data, 'bytes').hex()}
    else:
        result['values'] = None
    return result
