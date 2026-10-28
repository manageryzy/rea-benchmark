"""Generate bounded probes from admitted descriptions, never from model code."""
from . import artifacts, contracts


def nodes(value):
    return {node['id']: node for node in value['types']}


def resolve(value, path):
    by_id, offset, expression = nodes(value), 0, 'value'
    node = by_id[value['root']]
    for part in path:
        if isinstance(part, str) and node['kind'] == 'record':
            found = next(((index, item) for index, item in enumerate(node['fields']) if item['role'] == part), None)
            if found is None:
                raise ValueError('Required semantic field is missing')
            index, item = found
            offset += item['offset']
            expression += '.f' + str(index)
            node = by_id[item['type']]
        elif type(part) is int and node['kind'] == 'array' and 0 <= part < node['count']:
            offset += part * node['stride']
            expression += '.items[' + str(part) + '].value'
            node = by_id[node['element']]
        else:
            raise ValueError('Semantic role has an incompatible type or array extent')
    if node['kind'] != 'integer':
        raise ValueError('Semantic leaf must be an integer')
    return node, offset, expression


def known_facts(value, family, condition):
    expected = contracts.gold(family, value['binary_sha256'])
    for path in contracts.paths(family):
        candidate, _, _ = resolve(value, path)
        given, _, _ = resolve(expected, path)
        if condition == 'layout_recover' and any(candidate[key] != given[key] for key in ('bits', 'signed')):
            raise ValueError('Description contradicts the scalar types supplied in the task')
    if condition != 'scalar_recover':
        return
    actual_nodes, expected_nodes = nodes(value), nodes(expected)
    def compare(actual, given):
        if actual['kind'] != given['kind']:
            raise ValueError('Description contradicts the supplied layout')
        if given['kind'] == 'record':
            if any(actual[key] != given[key] for key in ('size', 'alignment')):
                raise ValueError('Description contradicts the supplied record size/alignment')
            for item in given['fields']:
                candidate = next((field for field in actual['fields'] if field['role'] == item['role']), None)
                if candidate is None or candidate['offset'] != item['offset']:
                    raise ValueError('Description contradicts a supplied field offset')
                compare(actual_nodes[candidate['type']], expected_nodes[item['type']])
        elif given['kind'] == 'array':
            if any(actual[key] != given[key] for key in ('count', 'stride', 'alignment')):
                raise ValueError('Description contradicts the supplied array layout')
            compare(actual_nodes[actual['element']], expected_nodes[given['element']])
    compare(actual_nodes[value['root']], expected_nodes[expected['root']])


def declarations(value):
    by_id, names, ordered = nodes(value), {}, []
    def emit(name):
        if name in names:
            return names[name]
        node = by_id[name]
        if node['kind'] == 'integer':
            cpp = ('std::int' if node['signed'] else 'std::uint') + str(node['bits']) + '_t'
            names[name] = cpp
            return cpp
        children = [item['type'] for item in node['fields']] if node['kind'] == 'record' else [node['element']]
        for child in children:
            emit(child)
        cpp = 'Type' + str(len(names))
        names[name] = cpp
        if node['kind'] == 'array':
            child = names[node['element']]
            cell = cpp + 'Cell'
            text = f'struct __attribute__((packed)) {cell} {{ {child} value;'
            text += f' unsigned char padding[{node["stride"]} - sizeof({child})];' if node['stride'] > size(value, node['element']) else ''
            text += f' }};\nstatic_assert(sizeof({cell}) == {node["stride"]});\n'
            text += f'struct __attribute__((packed, aligned({node["alignment"]}))) {cpp} {{ {cell} items[{node["count"]}]; }};\n'
            expected_size = node['count'] * node['stride']
        else:
            text, position = f'struct __attribute__((packed, aligned({node["alignment"]}))) {cpp} {{\n', 0
            for index, item in sorted(enumerate(node['fields']), key=lambda pair: pair[1]['offset']):
                if item['offset'] > position:
                    text += f'    unsigned char padding{index}[{item["offset"] - position}];\n'
                text += f'    {names[item["type"]]} f{index};\n'
                position = item['offset'] + size(value, item['type'])
            if node['size'] > position:
                text += f'    unsigned char tail[{node["size"] - position}];\n'
            text += '};\n'
            for index, item in enumerate(node['fields']):
                text += f'static_assert(__builtin_offsetof({cpp}, f{index}) == {item["offset"]});\n'
            expected_size = node['size']
        text += f'static_assert(sizeof({cpp}) == {expected_size});\nstatic_assert(alignof({cpp}) == {node["alignment"]});\n'
        ordered.append(text)
        return cpp
    root = emit(value['root'])
    return ''.join(ordered) + 'using Candidate = ' + root + ';\n'


def size(value, name):
    node = nodes(value)[name]
    return node['bits'] // 8 if node['kind'] == 'integer' else node['size'] if node['kind'] == 'record' else node['count'] * node['stride']


def coherent_abi(value, family):
    expected = contracts.abi_claim(family, value['convention'], value['binary_sha256'], value['entry_rva'])
    def normalized(source):
        return {**source, 'preserved': sorted(source['preserved']),
                'arguments': sorted(source['arguments'], key=lambda item: item['role'])}
    return normalized(value) == normalized(expected)


def generate(value, family, convention, claim=None):
    artifacts.types(value)
    x = lambda *path: resolve(value, path)[2]
    paths = contracts.paths(family)
    seed = ['auto* raw = reinterpret_cast<std::uint8_t*>(&value);',
            'for (std::size_t i = 0; i < sizeof(value); ++i) raw[i] = static_cast<std::uint8_t>((nonce >> (8 * (i % 8))) + 37 * i + 0x51);']
    body = []
    if family in ('record_codec', 'numeric_aggregation'):
        d, w = x('delta'), x('weight')
        seed += [d + ' = nonce ^ (nonce >> 17);', w + ' = nonce * 0x9e3779b1U;']
        body += [f'const auto old_delta = {d};', f'const auto old_weight = {w};',
                 f'{d} = old_delta < 0 ? old_delta - (nonce & 63) : old_delta + (nonce & 31);',
                 f'{w} = (old_weight ^ static_cast<std::uint32_t>(nonce)) + (old_delta < 0 ? 0x10203U : 0xa1b2c3d4U);']
        if family == 'record_codec':
            seed += [x('flags') + ' = nonce >> 32;']
            for index in range(3):
                seed += [f'{x("values", index)} = (nonce >> {index * 9}) + 0x80ff0103U * {index + 1};']
            body[0:0] = ['std::uint32_t saved[3] = {' + ', '.join(x('values', i) for i in range(3)) + '};']
            body += [f'{x("flags")} = ({x("flags")} + ((nonce >> 8) & 255)) ^ (old_weight >> 24);']
            for index in range(3):
                body += [f'{x("values", index)} = (saved[{(index + 1) % 3}] + static_cast<std::uint32_t>(old_delta) * {index + 3}) ^ (old_weight >> {index + 1}) ^ static_cast<std::uint32_t>(nonce >> {index * 7});']
    else:
        seed += [x('kind') + ' = nonce >> 32;', x('length') + ' = nonce * 0x9e3779b1U;']
        body += [f'{x("kind")} = {x("kind")} ^ (nonce >> 9);']
        for index in range(2):
            key, change = x('items', index, 'key'), x('items', index, 'change')
            seed += [f'{key} = (nonce >> {index * 13}) ^ 0xa71fU;', f'{change} = (nonce >> {index * 7}) ^ 0x80ffU;']
            body += [f'const auto change{index} = {change};',
                     f'{key} = {key} + (change{index} < 0 ? 257 : 13) + {index};',
                     f'{change} = change{index} + ((nonce >> {index * 11}) & 127);']
        body += [f'{x("length")} ^= static_cast<std::uint32_t>(nonce >> 16) + 0x6d350103U;']
    abi = '__attribute__((ms_abi))' if convention == 'ms_x64' else '__attribute__((sysv_abi))'
    source = '#include <cstdint>\n#include <cstddef>\n' + declarations(value)
    source += 'static Candidate seed(std::uint64_t nonce) { Candidate value;\n' + '\n'.join(seed) + '\nreturn value; }\n'
    source += 'static Candidate transform(Candidate value, std::uint64_t nonce) {\n' + '\n'.join(body) + '\nreturn value; }\n'
    source += 'static void serialize(const Candidate& value, std::uint8_t* out) {\n'
    for index, path in enumerate(paths):
        source += f'{{ auto word = static_cast<std::uint64_t>({x(*path)}); for (unsigned i=0; i<8; ++i) out[{index * 8}+i] = static_cast<std::uint8_t>(word >> (8*i)); }}\n'
    source += '}\n'
    if claim is not None:
        from .machine_codegen import wrappers
        source += 'extern "C" void* memcpy(void*, const void*, std::size_t);\n'
        source += 'extern "C" void generated_transform(const std::uint8_t* input, std::uint64_t nonce, std::uint8_t* out) { Candidate value; memcpy(&value,input,sizeof(value)); value=transform(value,nonce); memcpy(out,&value,sizeof(value)); }\n'
        source += 'extern "C" void generated_seed(std::uint64_t nonce, std::uint8_t* out) { auto value=seed(nonce); memcpy(out,&value,sizeof(value)); }\n'
        source += 'extern "C" void generated_serialize(const std::uint8_t* input, std::uint8_t* out) { Candidate value; memcpy(&value,input,sizeof(value)); serialize(value,out); }\n'
        return source + wrappers(claim, size(value, value['root']))
    source += f'extern "C" __attribute__((noinline, noipa)) Candidate {abi} recovered_slot(Candidate value, std::uint64_t nonce) {{ return transform(value, nonce); }}\n'
    source += f'extern "C" Candidate {abi} original_callee(Candidate, std::uint64_t);\n'
    source += 'extern "C" __attribute__((noinline, noipa)) std::int64_t generated_bridge(std::uint64_t nonce, std::uint8_t* out) { auto value = original_callee(seed(nonce), nonce); serialize(value, out); return 0; }\n'
    return source
