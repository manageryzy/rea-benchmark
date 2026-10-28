"""Integer-aggregate AMD64 bridges whose transfers use submitted machine locations.

The outer probe entry is always SysV. Inner call/return locations and preservation
are taken from the admitted ABI artifact, rather than delegated to an attribute.
"""
GPRS = ('rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15')
SYSV_PRESERVED = ('rbx', 'rbp', 'rsp', 'r12', 'r13', 'r14', 'r15')
FRAME = 768


def saved(name):
    if name == 'rbp':
        return 'qword ptr [rbp]'
    if name == 'rsp':
        raise ValueError('RSP is not a supported argument register')
    return 'qword ptr [rbp - ' + str(384 + 8 * GPRS.index(name)) + ']'


def start(name):
    lines = [f'.globl {name}', f'.type {name}, @function', name + ':',
             'push rbp', 'mov rbp, rsp', f'sub rsp, {FRAME}']
    lines += [f'mov {saved(reg)}, {reg}' for reg in GPRS]
    lines += [f'movdqu xmmword ptr [rbp - {512 + 16 * index}], xmm{index}' for index in range(16)]
    return lines


def restore(registers):
    lines = []
    for name in registers:
        if name in ('rbp', 'rsp'):
            continue  # Restored by LEAVE/RET, with host return-stack checks.
        if name.startswith('xmm'):
            index = int(name[3:])
            lines += [f'movdqu {name}, xmmword ptr [rbp - {512 + 16 * index}]']
        else:
            lines += [f'mov {name}, {saved(name)}']
    return lines


def wrappers(claim, width):
    arguments = {item['role']: item for item in claim['arguments']}
    record, nonce = arguments['record'], arguments['nonce']
    result, hidden = claim['return'], claim['return']['hidden_pointer']
    replacement = start('recovered_slot')
    if record['passing'] == 'register_value':
        for index, location in enumerate(record['locations']):
            replacement += [f'mov rax, {saved(location["name"])}', f'mov qword ptr [rbp - 128 + {index * 8}], rax']
    else:
        if record['passing'] == 'indirect_value':
            replacement += [f'mov r11, {saved(record["locations"][0]["name"])}']
            source = 'r11'
            base = 0
        else:
            source, base = 'rbp', 8 + record['locations'][0]['offset']
        for offset in range(0, width, 8):
            replacement += [f'mov rax, qword ptr [{source} + {base + offset}]', f'mov qword ptr [rbp - 128 + {offset}], rax']
    replacement += ['lea rdi, [rbp - 128]', f'mov rsi, {saved(nonce["locations"][0]["name"])}',
                    'lea rdx, [rbp - 256]', 'call generated_transform']
    if hidden:
        replacement += [f'mov r11, {saved(hidden)}']
        for offset in range(0, width, 8):
            replacement += [f'mov rax, qword ptr [rbp - 256 + {offset}]', f'mov qword ptr [r11 + {offset}], rax']
    # Force preservation to matter even if the helper happens not to use a
    # particular register in this compiler version. Restore only claimed saves.
    replacement += [f'mov {name}, 0x5365432112345678' for name in GPRS]
    replacement += [f'pxor xmm{index}, xmm{index}' for index in range(16)]
    replacement += restore(claim['preserved'])
    for index, location in enumerate(result['locations']):
        replacement += [f'mov {location["name"]}, {saved(hidden)}'] if hidden else [
            f'mov {location["name"]}, qword ptr [rbp - 256 + {index * 8}]']
    replacement += ['leave']
    if 'rbp' not in claim['preserved']:
        replacement += ['mov rbp, 0x5365432112345678']
    replacement += ['ret', '.size recovered_slot, .-recovered_slot']

    bridge = start('generated_bridge')
    # Memory immediately above the outgoing call area belongs to this frame.
    # A callee using an undeclared home area must not silently overwrite it.
    for offset in range(0, 32, 8):
        bridge += ['mov rax, 0x6d355173a71f80ff', f'mov qword ptr [rbp - 768 + {offset}], rax']
    bridge += [f'mov rdi, {saved("rdi")}', 'lea rsi, [rbp - 128]', 'call generated_seed']
    stack_bytes = max([claim['shadow_space'], *[item['offset'] - 8 + item['size'] for item in record['locations'] if item['kind'] == 'stack']])
    stack_bytes = ((stack_bytes + claim['stack_alignment'] - 1) // claim['stack_alignment']) * claim['stack_alignment']
    if stack_bytes:
        bridge += [f'sub rsp, {stack_bytes}']
    if record['passing'] == 'register_value':
        for index, location in enumerate(record['locations']):
            bridge += [f'mov {location["name"]}, qword ptr [rbp - 128 + {index * 8}]']
    elif record['passing'] == 'indirect_value':
        bridge += [f'lea {record["locations"][0]["name"]}, [rbp - 128]']
    else:
        for offset in range(0, width, 8):
            bridge += [f'mov rax, qword ptr [rbp - 128 + {offset}]',
                       f'mov qword ptr [rsp + {record["locations"][0]["offset"] - 8 + offset}], rax']
    if hidden:
        bridge += [f'lea {hidden}, [rbp - 256]']
    bridge += [f'mov {nonce["locations"][0]["name"]}, {saved("rdi")}', 'call original_callee']
    if stack_bytes:
        bridge += [f'add rsp, {stack_bytes}']
    # R11 is not an argument/return register in the supported integer profiles.
    bridge += ['mov r11, 0x6d355173a71f80ff']
    for offset in range(0, 32, 8):
        bridge += [f'cmp qword ptr [rbp - 768 + {offset}], r11', 'jne .Lbridge_bad']
    if hidden:
        bridge += ['lea r11, [rbp - 256]', f'cmp {result["locations"][0]["name"]}, r11', 'jne .Lbridge_bad']
    else:
        for index, location in enumerate(result['locations']):
            bridge += [f'mov qword ptr [rbp - 256 + {index * 8}], {location["name"]}']
    bridge += ['lea rdi, [rbp - 256]', f'mov rsi, {saved("rsi")}', 'call generated_serialize', 'xor eax, eax', 'jmp .Lbridge_return',
               '.Lbridge_bad:', 'mov eax, 1', '.Lbridge_return:']
    bridge += restore(SYSV_PRESERVED)
    bridge += ['leave', 'ret', '.size generated_bridge, .-generated_bridge']
    return '\nasm(R"probe(\n.intel_syntax noprefix\n.text\n' + '\n'.join(replacement + bridge) + '\n.att_syntax prefix\n)probe");\n'
