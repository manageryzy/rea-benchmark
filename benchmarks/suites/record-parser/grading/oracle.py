"""Reference semantics and deterministic hidden vectors, never exposed to models."""
import random

def score(b):
    if len(b) < 6: return -1
    if b[:2] != b'RE': return -2
    count = b[3]
    if count > 32 or len(b) != 6 + count: return -3
    acc = b[4] | b[5] << 8
    for i, byte in enumerate(b[6:]): acc = ((acc * 33) & 0xffffffff) ^ (byte + i)
    if b[2] == 0: return acc & 0xffff
    if b[2] == 1: return ((acc >> 1) if acc & 1 else (acc + 17)) & 0xffff
    if b[2] == 2: return (acc ^ 0xa5a5) & 0xffff
    return -4

def vectors(seed=20261019):
    rng = random.Random(seed)
    cases = []
    def add(b, category): cases.append({'hex': b.hex() or '-', 'expected': score(b), 'category': category})
    for n in range(6): add(bytes(range(n)), 'short')
    for mode in (0, 1, 2, 3, 255):
        for n in (0, 1, 2, 31, 32):
            for seed_value in (0, 1, 255, 256, 65535):
                for byte in (0, 1, 255):
                    add(b'RE'+bytes((mode,n))+seed_value.to_bytes(2,'little')+bytes([byte])*n, 'boundary')
    for _ in range(750):
        n=rng.randrange(33); mode=rng.randrange(3)
        add(b'RE'+bytes((mode,n))+rng.randbytes(2)+rng.randbytes(n), 'valid-random')
    for _ in range(250):
        b=bytearray(rng.randbytes(rng.randrange(6,50)))
        if rng.randrange(2): b[:2]=b'RE'
        add(bytes(b), 'malformed-random')
    for count in (33, 64, 255): add(b'RE'+bytes((0,count,0,0))+bytes(count), 'oversized-count')
    for n in range(33):
        add(b'RE'+bytes((0,n,0,0))+bytes(n+1), 'length-mismatch')
    return cases
