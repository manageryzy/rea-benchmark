"""Small bounded ELF reader for trusted target builds and stripped-image checks."""
import struct

HEADER = struct.Struct('<16sHHIQQQIHHHHHH')
PROGRAM = struct.Struct('<IIQQQQQQ')
SECTION = struct.Struct('<IIQQQQIIQQ')
SYMBOL = struct.Struct('<IBBHQQ')


def _slice(raw, start, size):
    if start < 0 or size < 0 or start > len(raw) or size > len(raw) - start:
        raise ValueError('ELF range exceeds file')
    return raw[start:start + size]


def _string(raw, offset):
    if not 0 <= offset < len(raw):
        raise ValueError('Invalid ELF string offset')
    end = raw.find(b'\0', offset)
    if end < 0:
        raise ValueError('Unterminated ELF string')
    return raw[offset:end].decode('utf-8', errors='strict')


class ELF:
    def __init__(self, raw):
        if not isinstance(raw, bytes) or not HEADER.size <= len(raw) <= 32 * 1024**2:
            raise ValueError('Invalid ELF size')
        self.raw = raw
        ident, kind, machine, version, entry, phoff, shoff, flags, size, phsize, phnum, shsize, shnum, shstr = HEADER.unpack_from(raw)
        if (ident[:7] != b'\x7fELF\x02\x01\x01' or kind not in (2, 3) or machine != 62 or version != 1
                or size != HEADER.size or phsize != PROGRAM.size or shsize != SECTION.size
                or not 1 <= phnum <= 128 or not 1 <= shnum <= 8192 or not shstr < shnum):
            raise ValueError('Unsupported target ELF')
        self.kind, self.entry = kind, entry
        _slice(raw, phoff, phnum * phsize)
        _slice(raw, shoff, shnum * shsize)
        self.programs = [PROGRAM.unpack_from(raw, phoff + index * phsize) for index in range(phnum)]
        self.sections = [SECTION.unpack_from(raw, shoff + index * shsize) for index in range(shnum)]
        self.strings = _slice(raw, self.sections[shstr][4], self.sections[shstr][5])
        self.base = min(program[3] & ~4095 for program in self.programs if program[0] == 1)

    def section_name(self, section):
        return _string(self.strings, section[0])

    def allocated(self):
        return {self.section_name(section): (section[3], section[2], section[5],
                _slice(self.raw, section[4], section[5]) if section[1] != 8 else None)
                for section in self.sections if section[2] & 2}

    def symbols(self):
        result = {}
        for section in self.sections:
            if section[1] != 2:
                continue
            if section[9] != SYMBOL.size or section[5] % SYMBOL.size or section[6] >= len(self.sections):
                raise ValueError('Invalid private symbol table')
            string_section = self.sections[section[6]]
            strings = _slice(self.raw, string_section[4], string_section[5])
            _slice(self.raw, section[4], section[5])
            for offset in range(section[4], section[4] + section[5], SYMBOL.size):
                name, info, visibility, index, address, size = SYMBOL.unpack_from(self.raw, offset)
                if name and index and address:
                    text = _string(strings, name)
                    value = {'address': address, 'rva': address - self.base, 'size': size, 'type': info & 15}
                    if text in result and result[text] != value:
                        raise ValueError('Ambiguous private symbol')
                    result[text] = value
        return result

    def bytes_at(self, rva, size):
        address = self.base + rva
        for program in self.programs:
            kind, flags, offset, start, physical, file_size, memory_size, align = program
            if kind == 1 and start <= address and address + size <= start + file_size:
                return _slice(self.raw, offset + address - start, size)
        raise ValueError('Truth points outside file-backed target memory')

    def segments(self):
        return [{'start': p[3] - self.base, 'end': p[3] - self.base + p[6],
                 'kind': 'code' if p[1] & 1 else 'data'} for p in self.programs if p[0] == 1]


def stripped_matches(debug, public):
    private, stripped = ELF(debug), ELF(public)
    if (private.kind != stripped.kind or private.entry != stripped.entry
            or private.programs != stripped.programs or private.allocated() != stripped.allocated()):
        raise ValueError('Stripping changed allocated code or data')
    if any(section[1] == 2 or stripped.section_name(section).startswith(('.debug', '.zdebug')) for section in stripped.sections):
        raise ValueError('Public target retains private symbol/debug metadata')
    if not any(private.section_name(section) == '.debug_info' for section in private.sections):
        raise ValueError('Private target has no same-build DWARF')
    return private, stripped
