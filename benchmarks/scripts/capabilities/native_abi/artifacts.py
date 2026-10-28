"""Task-scoped native aggregate ABI bindings; no source or arbitrary addresses."""
import re
from pathlib import Path

from grading.artifacts import decode

REGISTERS = ('rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r15')
LOADED_SOURCE = Path(__file__).read_bytes()


def binding(value, *, scalar=False):
    if not isinstance(value, dict) or value.get('kind') not in ('register', 'registers', 'indirect', 'stack'):
        raise ValueError('Invalid native ABI location')
    kind = value['kind']
    if kind == 'stack':
        if (set(value) != {'kind', 'offset'} or type(value['offset']) is not int
                or not 8 <= value['offset'] <= 512 or value['offset'] % 8):
            raise ValueError('Invalid native ABI stack offset')
    elif kind == 'registers':
        if (scalar or set(value) != {'kind', 'registers'} or not isinstance(value['registers'], list)
                or len(value['registers']) != 3 or any(reg not in REGISTERS for reg in value['registers'])
                or len(set(value['registers'])) != 3):
            raise ValueError('Invalid native ABI register aggregate')
    else:
        if (set(value) != {'kind', 'register'} or value['register'] not in REGISTERS
                or scalar != (kind == 'register')):
            raise ValueError('Invalid native ABI scalar/pointer binding')
    return value


def admit(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 8192:
        raise ValueError('Native ABI artifact exceeds its bound')
    value = decode(raw)
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'binary_sha256', 'packet', 'nonce', 'reply'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or not isinstance(value['binary_sha256'], str)
            or re.fullmatch('[0-9a-f]{64}', value['binary_sha256']) is None):
        raise ValueError('Invalid native ABI artifact contract')
    binding(value['packet'])
    binding(value['nonce'], scalar=True)
    binding(value['reply'])
    return value
