"""Separate arithmetic oracle; compiler and runtime workers never import it."""
from pathlib import Path

LOADED_SOURCE = Path(__file__).read_bytes()
MASK = 2**64 - 1
CALLER_XOR = 0x4c839e1720b6d5fa
NONCES = tuple(range(8)) + (0x8000000000000000, 0xffffffffffffffe1,
                            0xffffffffffffffff, 0x123456789abcdef0)


def rotate(value, amount):
    value &= MASK
    return ((value << amount) | (value >> (64 - amount))) & MASK


def arguments(nonce):
    if type(nonce) is not int or not 0 <= nonce <= MASK:
        raise ValueError('Invalid native ABI nonce')
    return {'nonce': nonce, 'first': (nonce * 0x10101 ^ 0x9361b72c40ad8ef5) & MASK,
            'second': rotate(nonce ^ 0x297ce813b5a406df, 17),
            'third': (0xe7b01c6a5294df83 - nonce * 65537) & MASK}


def expected(selected, values):
    if type(selected) is not int or selected not in (0, 1, 2):
        raise ValueError('Invalid native ABI selector')
    if set(values) != {'nonce', 'first', 'second', 'third'} or any(
            type(v) is not int or not 0 <= v <= MASK for v in values.values()):
        raise ValueError('Invalid native ABI reference arguments')
    nonce, a, b, c = (values[name] for name in ('nonce', 'first', 'second', 'third'))
    first = (rotate(a ^ nonce, 11) + b * 17) & MASK
    second = rotate(b + 0x7a6d503912c4b8ef, 23) ^ c
    third = ((c + nonce * 257) & MASK) ^ rotate(first + second, 5)
    if selected == 1:
        third ^= CALLER_XOR
    return [first, second, third]
