"""Independent T6 semantic oracle; no candidate generator or compiler imports."""
MASK = (1 << 64) - 1


def rotate(value, shift):
    value &= MASK
    return ((value << shift) | (value >> (64 - shift))) & MASK if shift else value


def observation(nonce):
    if type(nonce) is not int or not 0 <= nonce <= MASK:
        raise ValueError('Invalid indirect-call reference nonce')
    states = [(nonce ^ (((i + 1) * 0x9362a7d50bc14ef8) & MASK)) for i in range(2)]
    cookies = [(nonce * 0xd1342543de82ef95 + (i + 1) * 0x123456789abcdef) & MASK for i in range(2)]
    contexts = [((~nonce) + i * 0x23456789abcdef01) & MASK for i in range(2)]
    traces = [0, 0]
    operation = nonce & 1
    if (nonce >> 2) % 5 in (1, 2, 3):
        result = (-9) & MASK
    else:
        cookie = cookies[operation]
        if operation == 0:
            states[0] = (rotate(states[0] ^ nonce, nonce & 63) + cookie) & MASK
            value = states[0] ^ 0x6a09e667f3bcc909
        else:
            states[1] = (states[1] + (((nonce * 0x9e3779b97f4a7c15) & MASK) ^ cookie)) & MASK
            value = (states[1] + rotate(cookie, (nonce >> 1) & 63)) & MASK
        salt = nonce ^ (((operation + 1) * 0x9e3779b97f4a7c15) & MASK)
        bias = (nonce + (operation + 1) * 0x1122334455667788) & MASK
        if operation == 0:
            contexts[0] ^= rotate(value + salt, 17)
            traces[0] = 1
            result = (contexts[0] + bias) & MASK
        else:
            contexts[1] = (contexts[1] + (value ^ salt)) & MASK
            traces[1] = 3
            result = contexts[1] ^ bias
    words = [result, *states, *contexts, traces[0] | (traces[1] << 32), 1, 1]
    return b''.join(word.to_bytes(8, 'little') for word in words)
