"""Independent semantic oracle for T4 controls; never a compiler input."""
MASK = (1 << 64) - 1


def observation(nonce):
    if type(nonce) is not int or not 0 <= nonce <= MASK:
        raise ValueError('Invalid variant-object control nonce')
    state = nonce ^ 0xd5b72639012aef84
    cookie = (nonce * 0x9e3779b97f4a7c15 + 0x123456789abcdef0) & MASK
    mode = nonce % 6
    kind = 1 if mode in (1, 4) else 2 if mode == 2 else 0
    status, active = 0, 0
    if kind == 0:
        bits = (~nonce) & MASK if mode == 0 else 0x8000000000000000 if mode == 3 else 42
        shift = nonce & 63
        mixed = state ^ bits
        rotated = ((mixed << shift) | (mixed >> (64 - shift))) & MASK if shift else mixed
        state = (rotated + cookie) & MASK
        active = bits ^ cookie ^ nonce
    elif kind == 1:
        first = ((nonce & 0xffffffff) ^ 0x80000000) if mode == 1 else 0xfffffff9
        second = ((nonce >> 32) ^ 0xfedcba98) if mode == 1 else 0x12345678
        state = (state + (((first << 32) | second) ^ cookie)) & MASK
        active = (((first + (nonce & 0xffffffff)) & 0xffffffff) << 32) | (second ^ (cookie & 0xffffffff))
    else:
        status = -7
    return b''.join((word & MASK).to_bytes(8, 'little')
                    for word in (status, state, cookie, active, kind, 1, 1, 1))
