"""Generate physical ABI bridges from bounded submitted bindings.

The replacement independently computes the supplied semantics. It does not
decode and re-encode a claim before delegating to the same original, which could
hide symmetric ABI mistakes. The bridge calls the unchanged original callee.
No private gold binding or expected observation is imported here.
"""
from . import artifacts

REGS = ('rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15')
FRAME = 2040
LOGICAL, INPUT, OUTPUT, SNAPSHOT, RETURNS = 640, 720, 760, 1024, 1200

HELPER = '''#include <stdint.h>
struct Frame { uint64_t a, b, c, nonce, x, y, z; };
static uint64_t rotl(uint64_t x, unsigned n) { return (x << n) | (x >> (64 - n)); }
void __rea_native_compute(struct Frame *f) {
    f->x = rotl(f->a ^ f->nonce, 11) + f->b * 17;
    f->y = rotl(f->b + UINT64_C(0x7a6d503912c4b8ef), 23) ^ f->c;
    f->z = (f->c + f->nonce * 257) ^ rotl(f->x + f->y, 5);
}
'''


def saved(reg, *, returns=False):
    return (RETURNS if returns else SNAPSHOT) + REGS.index(reg) * 8


def generate(value, original_callee):
    if (type(original_callee) is not int or not 0x10000 <= original_callee < 2**32):
        raise ValueError('Native ABI original endpoint is outside the fixed profile')
    artifacts.binding(value['packet'])
    artifacts.binding(value['nonce'], scalar=True)
    artifacts.binding(value['reply'])
    code = ['.text']

    def emit(*lines):
        code.extend('    ' + line for line in lines)

    def begin(name):
        code.extend(['.globl ' + name, '.type ' + name + ',@function', name + ':'])
        emit(f'sub ${FRAME}, %rsp')
        for reg in REGS:
            emit(f'mov %{reg}, {saved(reg)}(%rsp)')

    def restore(excluded=()):
        for reg in REGS:
            if reg not in excluded:
                emit(f'mov {saved(reg)}(%rsp), %{reg}')

    def finish(name):
        emit(f'add ${FRAME}, %rsp', 'ret')
        code.append('.size ' + name + ', .-' + name)

    begin('__rea_native_replacement')
    packet = value['packet']
    if packet['kind'] == 'indirect':
        emit(f'mov {saved(packet["register"])}(%rsp), %r11')
    for index in range(3):
        if packet['kind'] == 'registers':
            emit(f'mov {saved(packet["registers"][index])}(%rsp), %r10')
        elif packet['kind'] == 'indirect':
            emit(f'mov {index * 8}(%r11), %r10')
        else:
            emit(f'mov {FRAME + packet["offset"] + index * 8}(%rsp), %r10')
        emit(f'mov %r10, {LOGICAL + index * 8}(%rsp)')
    nonce = value['nonce']
    location = saved(nonce['register']) if nonce['kind'] == 'register' else FRAME + nonce['offset']
    emit(f'mov {location}(%rsp), %r10', f'mov %r10, {LOGICAL + 24}(%rsp)',
         f'lea {LOGICAL}(%rsp), %rdi', 'call __rea_native_compute')
    reply = value['reply']
    if reply['kind'] == 'indirect':
        emit(f'mov {saved(reply["register"])}(%rsp), %r11')
    if reply['kind'] != 'registers':
        for index in range(3):
            emit(f'mov {LOGICAL + 32 + index * 8}(%rsp), %r10')
            destination = f'{index * 8}(%r11)' if reply['kind'] == 'indirect' else f'{FRAME + reply["offset"] + index * 8}(%rsp)'
            emit(f'mov %r10, {destination}')
    restore(reply['registers'] if reply['kind'] == 'registers' else ())
    if reply['kind'] == 'registers':
        for index, reg in enumerate(reply['registers']):
            emit(f'mov {LOGICAL + 32 + index * 8}(%rsp), %{reg}')
    elif reply['kind'] == 'indirect':
        emit(f'mov {saved(reply["register"])}(%rsp), %rax')
    finish('__rea_native_replacement')

    begin('__rea_native_bridge')
    for index in range(4):
        emit(f'mov {index * 8}(%rdi), %r10', f'mov %r10, {LOGICAL + index * 8}(%rsp)')
    for index in range(3):
        emit(f'mov {LOGICAL + index * 8}(%rsp), %r10', f'mov %r10, {INPUT + index * 8}(%rsp)',
             f'movq $0, {OUTPUT + index * 8}(%rsp)')
    # Fill stack arguments before assigning any live argument registers: R10
    # is a temporary here but can itself be a described argument register.
    if packet['kind'] == 'stack':
        for index in range(3):
            source = f'{LOGICAL + index * 8}(%rsp)'
            emit(f'mov {source}, %r10', f'mov %r10, {packet["offset"] - 8 + index * 8}(%rsp)')
    if nonce['kind'] == 'stack':
        emit(f'mov {LOGICAL + 24}(%rsp), %r10', f'mov %r10, {nonce["offset"] - 8}(%rsp)')
    if packet['kind'] == 'indirect':
        emit(f'lea {INPUT}(%rsp), %{packet["register"]}')
    elif packet['kind'] == 'registers':
        for index in range(3):
            emit(f'mov {LOGICAL + index * 8}(%rsp), %{packet["registers"][index]}')
    if nonce['kind'] == 'register':
        emit(f'mov {LOGICAL + 24}(%rsp), %{nonce["register"]}')
    if reply['kind'] == 'indirect':
        emit(f'lea {OUTPUT}(%rsp), %{reply["register"]}')
    emit('call *.Lnative_original(%rip)')
    for reg in REGS:
        emit(f'mov %{reg}, {saved(reg, returns=True)}(%rsp)')
    emit(f'mov {saved("rdi")}(%rsp), %r11')
    for index in range(3):
        if reply['kind'] == 'registers':
            source = saved(reply['registers'][index], returns=True)
        elif reply['kind'] == 'indirect':
            source = OUTPUT + index * 8
        else:
            source = reply['offset'] - 8 + index * 8
        emit(f'mov {source}(%rsp), %r10', f'mov %r10, {32 + index * 8}(%r11)')
    restore()
    finish('__rea_native_bridge')
    code.extend(['.section .rodata', '.p2align 3', '.Lnative_original:', f'.quad {original_callee}',
                 '.section .note.GNU-stack,"",@progbits', '.text'])
    return '\n'.join(code) + '\n', HELPER
