"""Host-owned SysV calls in an instruction emulator; the guest has no OS API."""
import struct

import unicorn as uc
from unicorn import x86_const as x86

from . import elf
from grading import returns

MASK = (1 << 64) - 1
STACK = 0x7FFF00000000
ARENA = 0x100000000
RETURN = 0x70000000
CALLBACK = 0x71000000
ARGUMENTS = [x86.UC_X86_REG_RDI, x86.UC_X86_REG_RSI, x86.UC_X86_REG_RDX,
             x86.UC_X86_REG_RCX, x86.UC_X86_REG_R8, x86.UC_X86_REG_R9]
PRESERVED = [x86.UC_X86_REG_RBX, x86.UC_X86_REG_RBP, x86.UC_X86_REG_R12,
             x86.UC_X86_REG_R13, x86.UC_X86_REG_R14, x86.UC_X86_REG_R15]
PREFIXES = {0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65, 0x66, 0x67}


def integer(value):
    if type(value) is not int or not -(1 << 63) <= value <= MASK:
        raise ValueError('An argument or callback result is outside uint64/int64')
    return value & MASK


def merge(ranges):
    result = []
    for begin, end in sorted(ranges):
        if begin == end:
            continue
        if result and begin <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([begin, end])
    return result


def allows(ranges, address, size):
    return size > 0 and any(begin <= address and address + size <= end for begin, end in ranges)


def relative_ranges(value, size, base):
    if not isinstance(value, list):
        raise ValueError('Memory access ranges must be explicit')
    result = []
    for pair in value:
        if (not isinstance(pair, list) or len(pair) != 2 or any(type(item) is not int for item in pair)
                or pair[0] < 0 or pair[1] < 0 or pair[0] > size or pair[1] > size - pair[0]):
            raise ValueError('Memory access range escapes its allocation')
        result.append([base + pair[0], base + pair[0] + pair[1]])
    return result


class Machine:
    def __init__(self, raw, entry, invocation, limits, state=None):
        self.limits = limits
        self.program = elf.load(raw, entry, limits)
        self.engine = uc.Uc(uc.UC_ARCH_X86, uc.UC_MODE_64)
        self.engine.ctl_set_cpu_model(x86.UC_CPU_X86_QEMU64)
        # MODE_64 alone does not enable the OS support bits needed by SSE2.
        # These are emulated registers, independent of the host control state.
        self.engine.reg_write(x86.UC_X86_REG_CR0, (self.engine.reg_read(x86.UC_X86_REG_CR0) | 2) & ~12)
        self.engine.reg_write(x86.UC_X86_REG_CR4, self.engine.reg_read(x86.UC_X86_REG_CR4) | 0x600)
        self.readable, self.writable, self.executable = [], [], []
        self.regions, self.callbacks, self.events = {}, {}, []
        self.instructions, self.error = 0, None
        if not isinstance(invocation, dict) or set(invocation) - {'id', 'regions', 'arguments', 'callbacks', 'return_type'}:
            raise ValueError('Invocation contains unknown fields; oracle data is forbidden')
        if type(invocation.get('id')) is not int or not 0 <= invocation['id'] < 10**12:
            raise ValueError('Host invocation ID must be a nonnegative integer')
        self.invocation_id = invocation['id']  # Never copied into guest memory.
        self.return_type = invocation.get('return_type', 'u64')
        if self.return_type not in ('u64', 'i64', 'u32', 'i32', 'void'):
            raise ValueError('Unsupported return type for the integer/pointer ABI')
        for segment in self.program['segments']:
            address, size, flags = segment['address'], segment['size'], segment['flags']
            self._map(address, size, (uc.UC_PROT_READ if flags & 4 else 0)
                      | (uc.UC_PROT_WRITE if flags & 2 else 0) | (uc.UC_PROT_EXEC if flags & 1 else 0))
            self.engine.mem_write(address, segment['data'])
            self.readable.append([address, address + size])
            if flags & 2:
                self.writable.append([address, address + size])
            if flags & 1:
                self.executable.append([address, address + size])
        if state is not None:
            expected = {segment['address']: segment['size'] for segment in self.program['segments'] if segment['flags'] & 2}
            if not isinstance(state, list) or len(state) != len(expected):
                raise ValueError('Persistent image snapshot does not match writable segments')
            seen = set()
            for item in state:
                if set(item) != {'address', 'data_hex'} or item['address'] in seen or item['address'] not in expected:
                    raise ValueError('Invalid persistent image snapshot')
                raw_state = bytes.fromhex(item['data_hex'])
                if len(raw_state) != expected[item['address']]:
                    raise ValueError('Persistent image snapshot has the wrong size')
                self.engine.mem_write(item['address'], raw_state)
                seen.add(item['address'])
        self._regions(invocation.get('regions', []))
        self._callbacks(invocation.get('callbacks', []))
        self._stack(invocation.get('arguments', []))
        for index, register in enumerate(PRESERVED):
            self.engine.reg_write(register, 0x13579BDF00001000 + index * 0x11111111)
        self.saved_registers = {register: self.engine.reg_read(register) for register in PRESERVED}
        self.engine.reg_write(x86.UC_X86_REG_EFLAGS, 0x202)
        for register, value in [(x86.UC_X86_REG_FS_BASE, 0), (x86.UC_X86_REG_GS_BASE, 0),
                                (x86.UC_X86_REG_FPCW, 0x37F), (x86.UC_X86_REG_FPTAG, 0xFFFF),
                                (x86.UC_X86_REG_FPSW, 0), (x86.UC_X86_REG_MXCSR, 0x1F80)]:
            self.engine.reg_write(register, value)
        self.control_registers = {register: self.engine.reg_read(register) for register in
                                  (x86.UC_X86_REG_FS_BASE, x86.UC_X86_REG_GS_BASE, x86.UC_X86_REG_FPCW,
                                   x86.UC_X86_REG_CR0, x86.UC_X86_REG_CR4)}
        self._map(RETURN, 4096, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
        self.engine.mem_write(RETURN, b'\x0f\x0b')
        self.readable.append([RETURN, RETURN + 2])
        self.readable, self.writable = merge(self.readable), merge(self.writable)
        self.executable = merge(self.executable)
        self.engine.hook_add(uc.UC_HOOK_CODE, self._instruction)
        self.engine.hook_add(uc.UC_HOOK_MEM_READ | uc.UC_HOOK_MEM_WRITE, self._memory)
        self.engine.hook_add(uc.UC_HOOK_MEM_INVALID, self._invalid_memory)
        self.engine.hook_add(uc.UC_HOOK_INTR, self._interrupt)

    def _map(self, address, size, permissions):
        begin = address & -elf.PAGE
        end = (address + max(size, 1) + elf.PAGE - 1) & -elf.PAGE
        self.engine.mem_map(begin, end - begin, permissions)

    def _regions(self, items):
        if not isinstance(items, list) or len(items) > self.limits['max_regions']:
            raise ValueError('Too many memory allocations')
        total = 0
        for index, item in enumerate(items):
            if set(item) != {'id', 'data_hex', 'readable', 'writable'} or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 64 or item['id'] in self.regions:
                raise ValueError('Invalid or duplicate allocation ID')
            data = bytes.fromhex(item['data_hex'])
            total += len(data)
            if total > self.limits['max_region_bytes']:
                raise ValueError('Invocation memory exceeds the profile')
            address = ARENA + index * 0x200000
            self._map(address, len(data), uc.UC_PROT_READ | uc.UC_PROT_WRITE)
            if data:
                self.engine.mem_write(address, data)
            self.regions[item['id']] = {'address': address, 'size': len(data), 'before': data}
            self.readable += relative_ranges(item['readable'], len(data), address)
            self.writable += relative_ranges(item['writable'], len(data), address)

    def _callbacks(self, items):
        if not isinstance(items, list) or len(items) > self.limits['max_callbacks']:
            raise ValueError('Too many declared callbacks')
        for index, item in enumerate(items):
            if set(item) != {'id', 'returns'} or not isinstance(item['id'], str) or not 1 <= len(item['id']) <= 64 or any(c['id'] == item['id'] for c in self.callbacks.values()):
                raise ValueError('Invalid callback descriptor')
            if not isinstance(item['returns'], list) or len(item['returns']) > self.limits['max_callback_calls']:
                raise ValueError('Callback response budget is invalid')
            address = CALLBACK + index * elf.PAGE
            self._map(address, elf.PAGE, uc.UC_PROT_READ | uc.UC_PROT_EXEC)
            self.engine.mem_write(address, b'\xc3')
            self.callbacks[address] = {'id': item['id'], 'returns': [integer(value) for value in item['returns']], 'calls': 0}
            self.readable.append([address, address + 1])
            self.executable.append([address, address + 1])

    def _argument(self, value):
        if type(value) is int:
            return integer(value)
        if not isinstance(value, dict):
            raise ValueError('Invalid argument')
        if set(value) == {'region', 'offset'} and value['region'] in self.regions:
            region = self.regions[value['region']]
            if type(value['offset']) is not int or not 0 <= value['offset'] <= region['size']:
                raise ValueError('Pointer escapes its allocation')
            return region['address'] + value['offset']
        if set(value) == {'callback'}:
            return next((address for address, item in self.callbacks.items() if item['id'] == value['callback']), None) or self._bad_argument()
        raise ValueError('Invalid pointer/callback argument')

    @staticmethod
    def _bad_argument():
        raise ValueError('Undeclared callback argument')

    def _stack(self, arguments):
        if not isinstance(arguments, list) or len(arguments) > self.limits['max_arguments']:
            raise ValueError('Argument count exceeds the SysV profile')
        self._map(STACK, self.limits['stack_bytes'], uc.UC_PROT_READ | uc.UC_PROT_WRITE)
        self.stack_pointer = STACK + self.limits['stack_bytes'] - 4096 - 8
        self.engine.reg_write(x86.UC_X86_REG_RSP, self.stack_pointer)
        self.engine.mem_write(self.stack_pointer, struct.pack('<Q', RETURN))
        values = [self._argument(value) for value in arguments]
        for register, value in zip(ARGUMENTS, values):
            self.engine.reg_write(register, value)
        extra = values[len(ARGUMENTS):]
        for index, value in enumerate(extra):
            self.engine.mem_write(self.stack_pointer + 8 + index * 8, struct.pack('<Q', value))
        self.readable += [[STACK, self.stack_pointer + 8 + 8 * len(extra)]]
        # The caller-owned return slot and stack above the argument area cannot
        # be written. Stack-passed parameter slots may be modified by a callee.
        self.writable += [[STACK, self.stack_pointer], [self.stack_pointer + 8, self.stack_pointer + 8 + 8 * len(extra)]]

    def _fail(self, status, address=None):
        if self.error is None:
            self.error = {'status': status}
            if address is not None:
                self.error['address'] = address
        self.engine.emu_stop()

    def _memory(self, engine, access, address, size, value, user):
        ranges = self.writable if access == uc.UC_MEM_WRITE else self.readable
        if not allows(ranges, address, size):
            self._fail('forbidden_memory_write' if access == uc.UC_MEM_WRITE else 'forbidden_memory_read', address)

    def _invalid_memory(self, engine, access, address, size, value, user):
        self._fail('unmapped_or_protected_memory', address)
        return False

    def _interrupt(self, engine, number, user):
        self._fail('guest_trap')

    def _instruction(self, engine, address, size, user):
        self.instructions += 1
        if not 1 <= size <= 15:
            self._fail('unsupported_guest_instruction', address)
            return
        if not allows(self.executable, address, size):
            self._fail('forbidden_instruction_fetch', address)
            return
        raw = bytes(engine.mem_read(address, size))
        offset = 0
        while offset < len(raw) and (raw[offset] in PREFIXES or 0x40 <= raw[offset] <= 0x4F):
            offset += 1
        opcode = raw[offset:]
        if opcode[:2] in (b'\x0f\x05', b'\x0f\x34'):
            self._fail('forbidden_syscall', address)
            return
        privileged = {0xCC, 0xCD, 0xCE, 0xCF, 0xF4, 0xFA, 0xFB, 0xE4, 0xE5, 0xE6, 0xE7,
                      0xEC, 0xED, 0xEE, 0xEF, 0x6C, 0x6D, 0x6E, 0x6F}
        systems = {0x01, 0x06, 0x08, 0x09, 0x20, 0x21, 0x22, 0x23, 0x30, 0x31, 0x32, 0xA2}
        if (not opcode or opcode[0] in privileged
                or len(opcode) > 1 and opcode[0] == 0x0F and opcode[1] in systems
                or len(opcode) > 2 and opcode[:2] == b'\x0f\xc7' and opcode[2] & 0xF8 in (0xF0, 0xF8)):
            self._fail('unsupported_system_instruction', address)
            return
        if opcode[:2] == b'\x0f\x0b':
            self._fail('guest_trap', address)
            return
        callback = self.callbacks.get(address)
        if callback is not None:
            index = callback['calls']
            if index >= len(callback['returns']) or len(self.events) >= self.limits['max_callback_calls']:
                self._fail('callback_budget', address)
                return
            result = callback['returns'][index]
            self.events.append({'id': callback['id'], 'arguments': [engine.reg_read(register) for register in ARGUMENTS], 'return': result})
            callback['calls'] += 1
            engine.reg_write(x86.UC_X86_REG_RAX, result)

    def run(self):
        try:
            self.engine.emu_start(self.program['entry'], RETURN, timeout=self.limits['emulator_timeout_us'], count=self.limits['instructions'])
        except uc.UcError:
            if self.error is None:
                self.error = {'status': 'invalid_guest_instruction_or_memory'}
        if self.error is None:
            if self.engine.reg_read(x86.UC_X86_REG_RIP) != RETURN:
                self.error = {'status': 'instruction_budget' if self.instructions >= self.limits['instructions'] else 'execution_timeout_or_no_return'}
            elif self.engine.reg_read(x86.UC_X86_REG_RSP) != self.stack_pointer + 8:
                self.error = {'status': 'caller_stack_corruption'}
            elif any(self.engine.reg_read(register) != value for register, value in self.saved_registers.items()):
                self.error = {'status': 'callee_saved_register_corruption'}
            elif self.engine.reg_read(x86.UC_X86_REG_EFLAGS) & (1 << 10):
                self.error = {'status': 'direction_flag_corruption'}
            elif (any(self.engine.reg_read(register) != value for register, value in self.control_registers.items())
                  or self.engine.reg_read(x86.UC_X86_REG_MXCSR) & 0xFFC0 != 0x1F80):
                self.error = {'status': 'caller_control_state_corruption'}
        record = None
        if self.error is None:
            if self.return_type == 'void':
                kind, value = 'bytes', b''
                value_bytes = b''
            else:
                width = 32 if self.return_type.endswith('32') else 64
                value = self.engine.reg_read(x86.UC_X86_REG_RAX) & ((1 << width) - 1)
                if self.return_type.startswith('i') and value >= 1 << (width - 1):
                    value -= 1 << width
                kind, value_bytes = 'int', str(value).encode('ascii')
            record = {'invocation_id': self.invocation_id, 'status': 'ok', 'value_kind': kind,
                      'declared_type': self.return_type, 'length': len(value_bytes), 'value_hex': value_bytes.hex(),
                      'frame_hex': returns.encode(self.invocation_id, value, kind).hex()}
        return {'schema_version': 1, 'invocation_id': self.invocation_id, 'provenance': 'host_emulator_return_v1',
                'status': self.error['status'] if self.error else 'returned', 'error': self.error,
                'return_record': record,
                'return_registers': None if self.error else {'rax': self.engine.reg_read(x86.UC_X86_REG_RAX), 'rdx': self.engine.reg_read(x86.UC_X86_REG_RDX)},
                'instructions': self.instructions, 'callbacks': self.events,
                'regions': [{'id': name, 'data_hex': bytes(self.engine.mem_read(item['address'], item['size'])).hex() if item['size'] else ''} for name, item in self.regions.items()],
                'state': [{'address': segment['address'], 'data_hex': bytes(self.engine.mem_read(segment['address'], segment['size'])).hex()}
                          for segment in self.program['segments'] if segment['flags'] & 2],
                'elf_sha256': self.program['sha256']}
