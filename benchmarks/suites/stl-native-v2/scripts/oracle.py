"""Independent behavioral specification; no authoring-code imports."""
import collections
import heapq
import random
import struct


def joined(values):
    return ",".join(map(str, values)) if values else "-"


def pairs(values):
    return joined([f"{key}:{value}" for key, value in values])


def expected(data, standard):
    if not data or data[0] >= 22:
        return "ERR:POINT"
    if len(data) % 2 == 0:
        return "ERR:LEN"
    point = data[0]
    values = list(struct.unpack("<" + "h" * ((len(data) - 1) // 2), data[1:]))
    if point == 0:
        return joined([x for x in values[1:] + values[:1] if x % 2])
    if point == 1:
        return joined(values[::-1]) if len(values) == 8 else "ERR:LEN"
    if point == 2:
        window = collections.deque()
        for index, value in enumerate(values):
            (window.appendleft if index % 2 == 0 else window.append)(value)
            if len(window) > 5:
                window.pop()
        return joined(list(window))
    if point == 3:
        return joined(sorted(set(values), reverse=True))
    if point == 4:
        return joined(sorted({x for x in values if x >= 0}))
    if point in (5, 9):
        return joined(sorted(set(values) - set(values[:1])))
    if point in (6, 10):
        return joined(sorted(values[1:]))
    if point == 7:
        totals = collections.defaultdict(int)
        for value in values:
            totals[value % 7] += value
        return pairs(sorted(totals.items()))
    if point == 8:
        return pairs(sorted(((x % 7, x) for x in values), key=lambda item: item[0]))
    if point == 11:
        return pairs(sorted(collections.Counter(x % 7 for x in values).items()))
    if point == 12:
        return pairs(sorted((x % 7, x) for x in values))
    if point in (13, 14, 15):
        state = []
        for value in values:
            if value >= 0:
                if point == 15:
                    heapq.heappush(state, -value)
                else:
                    state.append(value)
            elif state:
                if point == 15:
                    heapq.heappop(state)
                else:
                    state.pop(0 if point == 14 else -1)
        return joined(sorted([-x for x in state], reverse=True) if point == 15 else state[::-1] if point == 13 else state)
    if point == 16:
        return "".join(str(int(bool(x % 2) ^ (i % 3 == 0))) for i, x in enumerate(values)) or "-"
    if point == 17:
        return "".join(chr(97 + x % 7) for x in values) + "|" + str(len(values))
    if point == 18:
        bits = 0
        for value in values:
            bits ^= 1 << (value % 64)
        return f"{bits.bit_count()}|{bits:064b}"
    if point == 19:
        result = [x * 2 + 3 for x in values]
        return f"{sum(result)}|{joined(result)}"
    if point == 20:
        events, result, failed = [], [], False
        for index, value in enumerate(values, 1):
            events.append(index)
            if value < 0:
                failed = True
                break
            result.append(value * 3 + 9)
        events += [-x for x in events[::-1]]
        return f"{'ERR' if failed else 'OK'}|{joined(result)}|{joined(events)}"
    if standard == "c++11":
        return "ERR:STANDARD"
    result = [x * 3 + 6 for x in values]
    if result:
        result[0] += 1
    return f"{joined(sorted(result, reverse=True))}|{sum(x % 7 == 1 for x in values)}|1024"


def vectors():
    rng = random.Random(2026101902)
    cases = []
    directed = [[], [0], [1], [-1], [-32768, 32767], [7] * 12,
                list(range(8)), list(range(-128, 128)), [5, 5, -1, 0, -2, 5],
                [1, 8, 15, -6, 1, 8], list(range(64))]
    for point in range(22):
        inputs = list(directed)
        while len(inputs) < 128:
            size = rng.choice([0, 1, 2, 7, 8, 9, 32, 65, 128])
            inputs.append([rng.randint(-32768, 32767) if len(inputs) % 3 else rng.randint(-8, 8) for _ in range(size)])
        for index, values in enumerate(inputs):
            payload = bytes([point]) + struct.pack("<" + "h" * len(values), *values)
            cases.append({"point": point, "category": "directed" if index < len(directed) else "seeded_random", "hex": payload.hex()})
        cases.append({"point": point, "category": "malformed_length", "hex": bytes([point, 0]).hex()})
    for data in (b"", b"\xff", b"\x16", b"\xff\x00"):
        cases.append({"point": "dispatch", "category": "invalid_point", "hex": data.hex()})
    for case in cases:
        case["expected"] = {standard: expected(bytes.fromhex(case["hex"]), standard) for standard in ("c++11", "c++14")}
    return cases
