"""Independent arithmetic reference for original-only L6 calibration."""
MASK = (1 << 64) - 1
SALT = 0x93d50b7a6241ef8c
NONCES = tuple(range(8)) + (0x8000000000000000, 0xffffffffffffffe1,
                            0xffffffffffffffff, 0x123456789abcdef0)


def expected(selected, value):
    if type(selected) is not int or not 0 <= selected <= 8 or type(value) is not int or not 0 <= value <= MASK:
        raise ValueError('Invalid optimized-location reference input')
    salt = SALT ^ 0x10001 if selected == 8 else SALT
    shift = 9 if selected == 7 else 7
    mixed = value ^ salt
    rotated = ((mixed << shift) | (mixed >> (64 - shift))) & MASK
    return (rotated * 33 + 109) & MASK
