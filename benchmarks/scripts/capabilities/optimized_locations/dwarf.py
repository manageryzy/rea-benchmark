"""Same-build DWARF location candidates, not semantic equivalence certificates.

Only the pinned worker's DWARF-4/5 / x86-64 output is supported. Empty optimized
ranges are omitted; every reported byte must belong to file-backed executable
memory in the exact private/public build. Semantic review is a separate gate.
"""
import re
import struct

from capabilities import elf

DIE = re.compile(r'^\s*<(\d+)><([0-9a-f]+)>:\s+Abbrev Number:\s+(\d+)(?:\s+\((DW_TAG_\w+)\))?')
ATTRIBUTE = re.compile(r'^\s*<[0-9a-f]+>\s+DW_AT_(\w+)\s*:\s*(.*)$')


def number(text):
    match = re.fullmatch(r'(?:\([^)]*\)\s*)?(0x[0-9a-f]+|\d+)(?:\s+\([^)]*\))?', text.strip())
    if not match:
        raise ValueError('Unsupported DWARF integer attribute')
    return int(match[1], 16 if match[1].startswith('0x') else 10)


def text_value(text):
    return re.sub(r'^\([^)]*\):\s*', '', text).strip()


class Locations:
    def __init__(self, target):
        self.image, _ = elf.stripped_matches(bytes.fromhex(target['private_hex']), bytes.fromhex(target['public_hex']))
        text = target['debug_info']
        if not isinstance(text, str) or len(text.encode()) > 4 * 1024**2:
            raise ValueError('DWARF diagnostic exceeds its bound')
        self.dies, parents, current = {}, {}, None
        for line in text.splitlines():
            match = DIE.match(line)
            if match:
                depth, offset, abbreviation = int(match[1]), int(match[2], 16), int(match[3])
                for level in list(parents):
                    if level >= depth:
                        del parents[level]
                if not abbreviation:
                    current = None
                    continue
                if not match[4] or offset in self.dies or depth > 64 or len(self.dies) >= 100000:
                    raise ValueError('Invalid or unsupported DWARF DIE structure')
                current = {'offset': offset, 'tag': match[4], 'parent': parents.get(depth - 1), 'attributes': {}}
                self.dies[offset] = current
                parents[depth] = offset
            elif current is not None:
                attribute = ATTRIBUTE.match(line)
                if attribute:
                    if attribute[1] in current['attributes']:
                        raise ValueError('Duplicate DWARF attribute')
                    current['attributes'][attribute[1]] = attribute[2]
        if not self.dies:
            raise ValueError('Same-build DWARF contains no decoded DIEs')
        self.sections = {self.image.section_name(s): self.image.raw[s[4]:s[4] + s[5]]
                         for s in self.image.sections if self.image.section_name(s).startswith('.debug_')}
        self.units = []
        raw, index = self.sections.get('.debug_info', b''), 0
        while index < len(raw):
            if index + 6 > len(raw):
                raise ValueError('Truncated DWARF unit header')
            size, version = struct.unpack_from('<IH', raw, index)
            end = index + 4 + size
            if size >= 0xfffffff0 or end > len(raw) or version not in (4, 5):
                raise ValueError('Unsupported DWARF unit format')
            header = 11 if version == 4 else 12
            if size + 4 < header or (raw[index + 10] if version == 4 else raw[index + 7]) != 8:
                raise ValueError('Unsupported DWARF address size')
            if version == 5 and raw[index + 6] != 1:
                raise ValueError('Unsupported DWARF unit type')
            self.units.append((index, end, version))
            index = end
        if not self.units:
            raise ValueError('Missing same-build DWARF unit bytes')

    def attributes(self, offset):
        result, visited = {}, set()
        while offset is not None:
            if offset in visited or offset not in self.dies or len(visited) >= 32:
                raise ValueError('Cyclic or missing DWARF origin/specification')
            visited.add(offset)
            attributes = self.dies[offset]['attributes']
            result = {**attributes, **result}
            origin = attributes.get('abstract_origin', attributes.get('specification'))
            if origin is None:
                break
            match = re.fullmatch(r'<0x([0-9a-f]+)>', origin.strip())
            if not match:
                raise ValueError('Unsupported DWARF origin reference')
            offset = int(match[1], 16)
        return result

    def unit_base(self, offset):
        for _ in range(65):
            die = self.dies[offset]
            if die['tag'] == 'DW_TAG_compile_unit':
                return number(die['attributes'].get('low_pc', '0'))
            offset = die['parent']
            if offset is None:
                break
        raise ValueError('DWARF location has no compilation unit')

    def ranges(self, offset):
        # Physical ranges belong to this concrete DIE, not its abstract source
        # origin. Inheriting an origin's ranges would invent an optimized body.
        attributes = self.dies[offset]['attributes']
        ranges = []
        if 'ranges' in attributes:
            index, base = number(attributes['ranges']), self.unit_base(offset)
            units = [unit for unit in self.units if unit[0] <= offset < unit[1]]
            if len(units) != 1:
                raise ValueError('DWARF DIE lies outside its unit')
            version = units[0][2]
            data = self.sections.get('.debug_ranges' if version == 4 else '.debug_rnglists', b'')

            def fixed(size):
                nonlocal index
                if index < 0 or index + size > len(data):
                    raise ValueError('DWARF range list exceeds its section')
                value = int.from_bytes(data[index:index + size], 'little')
                index += size
                return value

            def uleb():
                value = 0
                for shift in range(0, 70, 7):
                    byte = fixed(1)
                    value |= (byte & 127) << shift
                    if not byte & 128:
                        if value >= 2**64:
                            break
                        return value
                raise ValueError('DWARF range integer exceeds 64 bits')

            for _ in range(4096):
                if version == 4:
                    start, end = fixed(8), fixed(8)
                    if start == end == 0:
                        break
                    if start == 2**64 - 1:
                        base = end
                        continue
                    start, end = base + start, base + end
                else:
                    opcode = fixed(1)
                    if opcode == 0:
                        break
                    if opcode == 5:  # DW_RLE_base_address
                        base = fixed(8)
                        continue
                    if opcode == 4:  # DW_RLE_offset_pair
                        start, end = base + uleb(), base + uleb()
                    elif opcode == 6:  # DW_RLE_start_end
                        start, end = fixed(8), fixed(8)
                    elif opcode == 7:  # DW_RLE_start_length
                        start = fixed(8)
                        end = start + uleb()
                    else:
                        raise ValueError('Unsupported DWARF range-list opcode')
                if start != end:
                    ranges.append((start, end))
            else:
                raise ValueError('DWARF range list has no bounded terminator')
        elif 'low_pc' in attributes and 'high_pc' in attributes:
            start, end = number(attributes['low_pc']), number(attributes['high_pc'])
            if end < start:
                end += start
            if start != end:
                ranges.append((start, end))
        result = []
        for start, end in ranges:
            start, end = start - self.image.base, end - self.image.base
            if not 0 <= start < end or end - start > 1024**2:
                raise ValueError('Invalid optimized code range')
            if not any(s['kind'] == 'code' and s['start'] <= start < end <= s['end'] for s in self.image.segments()):
                raise ValueError('DWARF code range lies outside executable memory')
            self.image.bytes_at(start, end - start)
            result.append({'start': start, 'end': end})
        return sorted(result, key=lambda r: (r['start'], r['end']))

    def candidates(self):
        result = []
        for offset, die in self.dies.items():
            if die['tag'] not in ('DW_TAG_subprogram', 'DW_TAG_inlined_subroutine'):
                continue
            ranges = self.ranges(offset)
            if not ranges:
                continue
            attributes = self.attributes(offset)
            owner = die['parent']
            while owner is not None and self.dies[owner]['tag'] != 'DW_TAG_subprogram':
                owner = self.dies[owner]['parent']
            owner_attributes = self.attributes(owner) if owner is not None else {}
            result.append({'die': offset, 'name': text_value(attributes.get('name', '')),
                           'linkage_name': text_value(attributes.get('linkage_name', '')),
                           'inlined': die['tag'] == 'DW_TAG_inlined_subroutine',
                           'owner': text_value(owner_attributes.get('name', '')),
                           'ranges': ranges, 'semantic_reviewed': False})
        return result
