"""Bounded ELF64 loader for already-linked freestanding AMD64 candidates."""
import hashlib
import re
import struct

HEADER = struct.Struct('<16sHHIQQQIHHHHHH')
PROGRAM = struct.Struct('<IIQQQQQQ')
SECTION = struct.Struct('<IIQQQQIIQQ')
SYMBOL = struct.Struct('<IBBHQQ')
IMAGE_START, IMAGE_END = 0x01000000, 0x03000000
PAGE = 4096


class UnsupportedELF(ValueError):
    pass


def checked_slice(raw, offset, size):
    if offset < 0 or size < 0 or offset > len(raw) or size > len(raw) - offset:
        raise UnsupportedELF('ELF file range is invalid')
    return raw[offset:offset + size]


def load(raw, entry, limits):
    if not isinstance(raw, bytes) or not HEADER.size <= len(raw) <= limits['max_elf_bytes']:
        raise UnsupportedELF('ELF size is outside the profile')
    if not isinstance(entry, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', entry):
        raise UnsupportedELF('Invalid function entry name')
    ident, kind, machine, version, start, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstrings = HEADER.unpack_from(raw)
    if (ident[:7] != b'\x7fELF\x02\x01\x01' or kind != 2 or machine != 62 or version != 1
            or ehsize != HEADER.size or phsize != PROGRAM.size or shsize != SECTION.size
            or not 1 <= phnum <= 32 or not 1 <= shnum <= 4096 or shstrings >= shnum):
        raise UnsupportedELF('Profile requires static little-endian ELF64 AMD64 ET_EXEC')
    checked_slice(raw, phoff, phsize * phnum)
    checked_slice(raw, shoff, shsize * shnum)
    segments, pages, total, writable = [], set(), 0, 0
    for index in range(phnum):
        ptype, permissions, offset, address, physical, file_size, memory_size, alignment = PROGRAM.unpack_from(raw, phoff + index * phsize)
        if ptype in (2, 3, 7):
            raise UnsupportedELF('Dynamic linking, interpreters and TLS are unsupported')
        if ptype != 1:
            continue
        if (permissions & ~7 or permissions & 3 == 3 or memory_size < file_size
                or alignment not in (0, 1) and (alignment & (alignment - 1) or alignment > 2**21)):
            raise UnsupportedELF('Invalid load-segment permissions or alignment')
        data = checked_slice(raw, offset, file_size)
        if not memory_size:
            continue
        if (not IMAGE_START <= address < IMAGE_END or memory_size > IMAGE_END - address
                or not permissions & 4 or alignment > 1 and address % alignment != offset % alignment):
            raise UnsupportedELF('Load segment is outside the restricted image')
        total += memory_size
        writable += memory_size if permissions & 2 else 0
        if total > limits['max_image_bytes'] or writable > limits['max_image_writable_bytes']:
            raise UnsupportedELF('Candidate image memory exceeds the profile')
        selected_pages = set(range(address // PAGE, (address + memory_size + PAGE - 1) // PAGE))
        if selected_pages & pages:
            raise UnsupportedELF('Overlapping load-segment pages')
        pages.update(selected_pages)
        segments.append({'address': address, 'size': memory_size, 'data': data, 'flags': permissions})
    if not segments:
        raise UnsupportedELF('No loadable candidate code')
    sections = [SECTION.unpack_from(raw, shoff + index * shsize) for index in range(shnum)]
    entries = []
    for section in sections:
        name, stype, sflags, address, offset, size, link, info, align, stride = section
        if stype != 8:
            checked_slice(raw, offset, size)
        if stype in (4, 9, 6, 14, 15, 16) and size:
            raise UnsupportedELF('Runtime relocations, dynamic metadata and initializers are unsupported')
        if stype != 2:
            continue
        if stride != SYMBOL.size or size % stride or not 0 <= link < shnum or sections[link][1] != 3:
            raise UnsupportedELF('Invalid ELF symbol table')
        strings = checked_slice(raw, sections[link][4], sections[link][5])
        for position in range(offset, offset + size, stride):
            string, sinfo, visibility, section_index, value, symbol_size = SYMBOL.unpack_from(raw, position)
            if string >= len(strings):
                raise UnsupportedELF('Invalid symbol string offset')
            end = strings.find(b'\0', string)
            if end < 0:
                raise UnsupportedELF('Unterminated ELF symbol name')
            symbol_name = strings[string:end]
            if section_index == 0 and symbol_name:
                raise UnsupportedELF('Undefined symbols require an unsupported runtime')
            if symbol_name != entry.encode('ascii'):
                continue
            if sinfo & 15 != 2 or sinfo >> 4 not in (1, 2) or not 0 < section_index < shnum:
                raise UnsupportedELF('Entry must be a defined externally visible function')
            if not any(segment['flags'] & 1 and segment['address'] <= value
                       and value + max(1, symbol_size) <= segment['address'] + segment['size'] for segment in segments):
                raise UnsupportedELF('Entry is not within executable candidate bytes')
            entries.append(value)
    if len(entries) != 1:
        raise UnsupportedELF('Exactly one matching function entry is required')
    # Additional exported functions can be invoked by name from the same linked
    # image. e_entry names the first compiler-selected export, not every call.
    if not any(segment['flags'] & 1 and segment['address'] <= start < segment['address'] + segment['size'] for segment in segments):
        raise UnsupportedELF('ELF entry point is outside executable bytes')
    return {'entry': entries[0], 'segments': segments, 'sha256': hashlib.sha256(raw).hexdigest()}
