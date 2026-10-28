"""Private controls for the exact pinned native-ABI original builds."""
from . import artifacts


def gold(language, binary_sha256):
    if language == 'rust':
        packet = {'kind': 'indirect', 'register': 'rsi'}
        nonce = {'kind': 'register', 'register': 'rdx'}
        reply = {'kind': 'indirect', 'register': 'rdi'}
    elif language == 'go':
        packet = {'kind': 'registers', 'registers': ['rax', 'rbx', 'rcx']}
        nonce = {'kind': 'register', 'register': 'rdi'}
        reply = {'kind': 'registers', 'registers': ['rax', 'rbx', 'rcx']}
    else:
        raise ValueError('Unsupported native ABI gold condition')
    return {'schema_version': 1, 'binary_sha256': binary_sha256,
            'packet': packet, 'nonce': nonce, 'reply': reply}


def ffi_control(binary_sha256):
    # The separate Rust extern-C control puts the 24-byte input on the stack.
    return {'schema_version': 1, 'binary_sha256': binary_sha256,
            'packet': {'kind': 'stack', 'offset': 8},
            'nonce': {'kind': 'register', 'register': 'rsi'},
            'reply': {'kind': 'indirect', 'register': 'rdi'}}
