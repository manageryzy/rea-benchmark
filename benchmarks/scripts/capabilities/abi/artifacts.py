"""Bounded graph and ABI admission; identifiers never become executable text."""
import copy
from grading.artifacts import object_fields, integer, identifier, digest, sequence, unique

REGISTERS = {'rax', 'rbx', 'rcx', 'rdx', 'rsi', 'rdi', 'rbp', 'rsp', *['r' + str(i) for i in range(8, 16)],
             *['xmm' + str(i) for i in range(16)]}


def alignment(value):
    integer(value, 1, 16)
    if value & (value - 1):
        raise ValueError('Alignment must be a supported power of two')
    return value


def types(value):
    object_fields(value, ('schema_version', 'binary_sha256', 'root', 'types'))
    integer(value['schema_version'], 1, 1)
    digest(value['binary_sha256'])
    identifier(value['root'])
    nodes = {}
    for node in sequence(value['types'], 32):
        if not isinstance(node, dict):
            raise ValueError('Type nodes must be objects')
        identifier(node.get('id'))
        if node['id'] in nodes:
            raise ValueError('Duplicate type identifier')
        kind = node.get('kind')
        if kind == 'integer':
            object_fields(node, ('id', 'kind', 'bits', 'signed'))
            if type(node['bits']) is not int or node['bits'] not in (8, 16, 32, 64) or type(node['signed']) is not bool:
                raise ValueError('Unsupported scalar representation')
        elif kind == 'record':
            object_fields(node, ('id', 'kind', 'size', 'alignment', 'fields'))
            integer(node['size'], 1, 128)
            alignment(node['alignment'])
            roles = []
            for item in sequence(node['fields'], 16):
                object_fields(item, ('role', 'offset', 'type'))
                roles.append(identifier(item['role']))
                identifier(item['type'])
                integer(item['offset'], 0, 127)
            unique(roles)
        elif kind == 'array':
            object_fields(node, ('id', 'kind', 'element', 'count', 'stride', 'alignment'))
            identifier(node['element'])
            integer(node['count'], 1, 8)
            integer(node['stride'], 1, 128)
            alignment(node['alignment'])
        else:
            raise ValueError('Unsupported type node')
        nodes[node['id']] = node
    done, active, heights = {}, set(), {}
    def visit(name, depth=0):
        if name in done:
            return done[name]
        if name not in nodes or name in active or depth > 32:
            raise ValueError('Unknown, cyclic or excessively deep type graph')
        active.add(name)
        node = nodes[name]
        if node['kind'] == 'integer':
            size, align = node['bits'] // 8, node['bits'] // 8
            height = 1
        elif node['kind'] == 'array':
            size, _ = visit(node['element'], depth + 1)
            if node['stride'] < size:
                raise ValueError('Array stride is smaller than its element')
            size, align = node['stride'] * node['count'], node['alignment']
            height = 1 + heights[node['element']]
        else:
            size, align = node['size'], node['alignment']
            ranges = []
            for item in node['fields']:
                width, _ = visit(item['type'], depth + 1)
                end = item['offset'] + width
                if end > size or any(item['offset'] < b and a < end for a, b in ranges):
                    raise ValueError('Overlapping fields or fields outside the record')
                ranges.append((item['offset'], end))
            height = 1 + max((heights[item['type']] for item in node['fields']), default=0)
        if height > 8:
            raise ValueError('Type graph exceeds eight levels')
        if size > 128 or size % align:
            raise ValueError('Type size is outside its alignment or size bound')
        active.remove(name)
        done[name] = size, align
        heights[name] = height
        return size, align
    for name in nodes:
        visit(name)
    if value['root'] not in nodes or nodes[value['root']]['kind'] != 'record':
        raise ValueError('The probe root must be a record')
    return value


def abi(value):
    object_fields(value, ('schema_version', 'binary_sha256', 'entry_rva', 'convention', 'arguments',
                          'return', 'stack_alignment', 'shadow_space', 'preserved'))
    value = copy.deepcopy(value)
    if isinstance(value['convention'], str): value['convention'] = value['convention'].lower()
    integer(value['schema_version'], 1, 1)
    digest(value['binary_sha256'])
    integer(value['entry_rva'])
    if value['convention'] not in ('sysv_amd64', 'ms_x64'):
        raise ValueError('Unsupported ABI family')
    alignment(value['stack_alignment'])
    integer(value['shadow_space'], 0, 128)
    def locations(items):
        for item in sequence(items, 4):
            if not isinstance(item, dict):
                raise ValueError('Machine locations must be objects')
            if item.get('kind') == 'register':
                object_fields(item, ('kind', 'name', 'size'))
                if isinstance(item['name'], str): item['name'] = item['name'].lower()
                if not isinstance(item['name'], str) or item['name'] not in REGISTERS:
                    raise ValueError('Unknown register')
            elif item.get('kind') == 'stack':
                object_fields(item, ('kind', 'offset', 'size'))
                integer(item['offset'], 0, 256)
            else:
                raise ValueError('Unknown machine location')
            integer(item['size'], 1, 128)
        unique(items)
    roles = []
    for item in sequence(value['arguments'], 4):
        object_fields(item, ('role', 'passing', 'locations'))
        roles.append(identifier(item['role']))
        if item['passing'] not in ('stack_value', 'register_value', 'indirect_value'):
            raise ValueError('Unknown parameter passing mode')
        locations(item['locations'])
    unique(roles)
    result = value['return']
    object_fields(result, ('passing', 'hidden_pointer', 'locations'))
    if isinstance(result['hidden_pointer'], str): result['hidden_pointer'] = result['hidden_pointer'].lower()
    if result['passing'] not in ('register_value', 'indirect_value') or result['hidden_pointer'] is not None and (not isinstance(result['hidden_pointer'], str) or result['hidden_pointer'] not in REGISTERS):
        raise ValueError('Unknown return representation')
    locations(result['locations'])
    value['preserved'] = [name.lower() if isinstance(name, str) else name for name in sequence(value['preserved'], 32)]
    for name in value['preserved']:
        if not isinstance(name, str) or name not in REGISTERS:
            raise ValueError('Unknown preserved register')
    unique(value['preserved'])
    return value
