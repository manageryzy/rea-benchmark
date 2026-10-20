"""Independent reference semantics and reproducible, point-labelled hidden checks."""

import heapq
import random
import re
import struct

SEED = 2026101901


def joined(items):
    return ",".join(str(item) for item in items) or "-"


def signed_words(data):
    return [item[0] for item in struct.iter_unpack("<h", data)]


def reference(request):
    if not request or request[0] > 5:
        return "ERR:POINT"
    point, body = request[0], request[1:]
    if point == 0:
        if any(value >= 128 for value in body):
            return "ERR:TEXT"
        words = re.findall(r"[a-z0-9_]+", body.decode("ascii").lower())
        counts = {word: words.count(word) for word in set(words)}
        return joined(f"{word}:{counts[word]}" for word in sorted(counts))
    if point == 1:
        if len(body) % 4:
            return "ERR:LEN"
        balances = {}
        for key, operation, amount in struct.iter_unpack("<BBh", body):
            if operation > 2:
                return "ERR:OP"
            if operation == 0:
                balances[key] = balances.get(key, 0) + amount
            elif operation == 1:
                balances[key] = amount
            else:
                balances.pop(key, None)
        entries = joined(f"{key}:{balances[key]}" for key in sorted(balances))
        return f"{sum(balances.values())}|{entries}"
    if point == 2:
        if len(body) % 3:
            return "ERR:LEN"
        records = sorted(struct.iter_unpack("<Bh", body), key=lambda item: -item[1])
        return joined(f"{key}:{score}" for key, score in records)
    if point == 3:
        if not body or (len(body) - 1) % 2:
            return "ERR:LEN"
        count = body[0]
        if not 1 <= count <= 16:
            return "ERR:NODES"
        pairs = list(struct.iter_unpack("BB", body[1:]))
        if any(source >= count or target >= count for source, target in pairs):
            return "ERR:EDGE"
        edges = set(pairs)
        incoming = [sum(target == node for _, target in edges) for node in range(count)]
        ready = [node for node in range(count) if incoming[node] == 0]
        heapq.heapify(ready)
        order = []
        while ready:
            node = heapq.heappop(ready)
            order.append(node)
            for source, target in edges:
                if source == node:
                    incoming[target] -= 1
                    if incoming[target] == 0:
                        heapq.heappush(ready, target)
        return joined(order) if len(order) == count else "CYCLE"
    if point == 4:
        if len(body) != 17:
            return "ERR:LEN"
        if body[0] > 1:
            return "ERR:MODE"
        values = signed_words(body[1:])
        weighted = sum(value * coefficient for value, coefficient in
                       zip(values, [1, 2, -3, 5, 7, -11, 13, -17]))
        a, b, c, d, e, f, g, h = values
        signature = (a ^ h) + (b ^ g) - (c ^ f) + (d ^ e)
        return f"{weighted}|{max(values) - min(values)}|{signature}"
    if not body or (len(body) - 1) % 2:
        return "ERR:LEN"
    if body[0] > 2:
        return "ERR:MODE"
    def apply(value):
        if body[0] == 0:
            return value * 3 + 7
        if body[0] == 1:
            return 500 + (value - 100) * 2 if value > 100 else value * 5
        return max(-20, value - 13)
    results = [apply(value) for value in signed_words(body[1:])]
    return f"{sum(results)}|{joined(results)}"


def vectors():
    rng = random.Random(SEED)
    cases = []

    def add(point, body, category, relation=None):
        request = bytes([point]) + body
        record = {"hex": request.hex(), "expected": reference(request),
                  "point": f"P{point}", "category": category}
        if relation:
            record["relation"] = relation
        cases.append(record)

    for point in range(6):
        start = len(cases)
        for length in range(35):
            add(point, bytes(length), "length_and_zero_boundaries")
        if point == 0:
            for value in range(256):
                add(point, bytes([value]), "character_boundaries")
            for text in [b"Hello HELLO hello", b"a-b_a 007 007", b"", b"A\x00a\tA!", b"___"]:
                add(point, text, "directed_words")
            for index in range(24):
                text = f"Word{index} word{index} ITEM".encode()
                assert reference(b"\0" + text) == reference(b"\0" + text.swapcase())
                add(point, text, "case_metamorphic", f"case-{index}")
                add(point, text.swapcase(), "case_metamorphic", f"case-{index}")
        elif point == 1:
            for key in [0, 1, 127, 255]:
                for amount in [-32768, -1, 0, 1, 32767]:
                    for operation in [0, 1, 2, 3, 255]:
                        add(point, struct.pack("<BBh", key, operation, amount), "signed_and_operations")
            add(point, b"".join(struct.pack("<BBh", *record) for record in
                               [(1, 0, 3), (1, 0, -7), (2, 1, 0), (1, 2, 99), (1, 0, 5)]), "state_transitions")
            for key in range(20):
                base = struct.pack("<BBh", key, 1, -100)
                erased = base + struct.pack("<BBh", key, 2, 0)
                assert reference(bytes([point]) + erased) == "0|-"
                add(point, erased, "erase_metamorphic")
        elif point == 2:
            for score in [-32768, -1, 0, 1, 32767]:
                for ids in [[3, 1, 3, 2], [255, 0, 127], [1] * 30]:
                    add(point, b"".join(struct.pack("<Bh", key, score) for key in ids), "stable_ties")
            add(point, b"".join(struct.pack("<Bh", key, score) for key, score in
                               [(1, 5), (2, -3), (3, 5), (4, 32767), (5, -32768)]), "signed_ranking")
        elif point == 3:
            for count in [0, 1, 2, 16, 17, 255]:
                add(point, bytes([count]), "node_boundaries")
            for count in range(1, 17):
                edges = bytes(value for node in range(count - 1) for value in [node, node + 1])
                add(point, bytes([count]) + edges, "linear_graph")
                add(point, bytes([count]) + edges + bytes([count - 1, 0]), "cycles")
                assert reference(bytes([point, count]) + edges) == reference(bytes([point, count]) + edges + edges)
                add(point, bytes([count]) + edges + edges, "duplicate_edge_metamorphic")
            add(point, bytes([4, 0, 3, 1, 3]), "heap_tie_order")
        elif point == 4:
            for position in range(8):
                for value in [-32768, -1, 0, 1, 32767]:
                    values = [0] * 8
                    values[position] = value
                    packed = struct.pack("<8h", *values)
                    for mode in [0, 1]:
                        add(point, bytes([mode]) + packed, "abi_argument_isolation")
            for mode in [0, 1, 2, 255]:
                add(point, bytes([mode]) + struct.pack("<8h", -32768, 32767, -1, 1, -200, 300, -400, 500), "abi_signed_aggregate")
        else:
            for mode in [0, 1, 2, 3, 255]:
                for value in [-32768, -8, -7, -6, 0, 99, 100, 101, 32767]:
                    add(point, bytes([mode]) + struct.pack("<h", value), "policy_thresholds")
                add(point, bytes([mode]), "empty_policy_sequence")
        while len(cases) - start < 512:
            index = len(cases) - start
            if index % 7 == 0:
                body = rng.randbytes(rng.randrange(0, 70))
                category = "random_malformed_or_valid"
            elif point == 0:
                words = [rng.choice([b"Alpha", b"beta", b"_key", b"007", b"Z9"]) for _ in range(rng.randrange(1, 55))]
                body = rng.choice([b" ", b"!", b"\0", b"\r\n"]).join(words)
                category = "random_words"
            elif point == 1:
                body = b"".join(struct.pack("<BBh", rng.randrange(16), rng.randrange(3), rng.randrange(-32768, 32768))
                                for _ in range(rng.randrange(0, 65)))
                category = "random_ledger"
            elif point == 2:
                body = b"".join(struct.pack("<Bh", rng.randrange(16), rng.choice([-32768, -5, 0, 0, 5, 32767]))
                                for _ in range(rng.randrange(0, 65)))
                category = "random_tied_records"
            elif point == 3:
                count = rng.randrange(1, 17)
                pairs = [(rng.randrange(count), rng.randrange(count)) for _ in range(rng.randrange(0, 60))]
                if index % 2:
                    pairs = [(min(a, b), max(a, b)) for a, b in pairs if a != b]
                body = bytes([count] + [value for pair in pairs for value in pair])
                category = "random_graph"
            elif point == 4:
                packed = struct.pack("<8h", *(rng.randrange(-32768, 32768) for _ in range(8)))
                body = bytes([index % 2]) + packed
                category = "random_abi"
            else:
                body = bytes([rng.randrange(3)]) + b"".join(struct.pack("<h", rng.randrange(-32768, 32768))
                                                           for _ in range(rng.randrange(0, 65)))
                category = "random_policy"
            add(point, body, category)
    for request in [b""] + [bytes([value]) for value in range(6, 21)]:
        cases.append({"hex": request.hex() or "-", "expected": reference(request),
                      "point": "dispatch", "category": "invalid_point"})
    assert len(cases) == 3088
    return cases
