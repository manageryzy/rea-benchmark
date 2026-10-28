"""Adapt one host invocation to Go ABIInternal, preserving host-owned returns."""
import copy
from unicorn import x86_const as x86
from grading.c_executor import machine as core
from . import go_image

REGISTERS = [x86.UC_X86_REG_RAX, x86.UC_X86_REG_RBX, x86.UC_X86_REG_RCX,
             x86.UC_X86_REG_RDI, x86.UC_X86_REG_RSI]


def observe(raw, invocation, limits):
    if (not isinstance(invocation, dict) or set(invocation) != {'id', 'regions', 'arguments', 'return_type', 'callbacks'}
            or invocation['callbacks'] != []
            or invocation['return_type'] != 'i32' or len(invocation['arguments']) != 5
            or [region['id'] for region in invocation['regions']] != ['arena', 'output_length']):
        raise ValueError('Go buffer accepts exactly one five-argument buffer invocation')
    program = go_image.load(raw, limits)
    supplied = copy.deepcopy(invocation)
    supplied['regions'].append({'id': 'go_thread', 'data_hex': bytes(64).hex(),
                               'readable': [[0, 64]], 'writable': []})

    def loader(artifact, entry, requested):
        if artifact != raw or entry != 'go_buffer' or requested != limits:
            raise ValueError('Go candidate loader request changed')
        return program

    old = core.elf.load
    core.elf.load = loader
    try:
        machine = core.Machine(raw, 'go_buffer', supplied, limits)
    finally:
        core.elf.load = old
    engine = machine.engine
    for register, argument in zip(REGISTERS, invocation['arguments']):
        engine.reg_write(register, machine._argument(argument))
    thread = machine.regions['go_thread']['address']
    engine.reg_write(x86.UC_X86_REG_R14, thread)
    # ABIInternal has no SysV callee-saved register contract. The supplied Go
    # context and immutable root return slot remain guarded independently.
    machine.saved_registers = {x86.UC_X86_REG_R14: thread}
    spill = [machine.stack_pointer + 8, machine.stack_pointer + 48]
    writable = [[core.STACK, machine.stack_pointer], spill]
    for region in invocation['regions']:
        allocation = machine.regions[region['id']]
        writable += core.relative_ranges(region['writable'], allocation['size'], allocation['address'])
    machine.writable = core.merge(writable)
    machine.readable = core.merge(machine.readable + [spill])
    # Runtime/candidate image data is read-only at the memory-hook boundary,
    # including nominally writable ELF segments. No startup or heap is provided.
    result = machine.run()
    result.pop('state', None)
    result['regions'] = [region for region in result['regions'] if region['id'] != 'go_thread']
    result['abi'] = 'go_abi_internal_amd64'
    return result
