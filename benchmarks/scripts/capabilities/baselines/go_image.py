"""Bounded static Go images for the nonallocating ABIInternal candidate profile."""
import hashlib
from pathlib import Path

from capabilities import elf
from capabilities.native_abi import images

LOADED = Path(__file__).read_bytes()


def load(raw, limits):
    if Path(__file__).read_bytes() != LOADED:
        raise RuntimeError('Loaded Go candidate image validator changed')
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= limits['max_elf_bytes']:
        raise ValueError('Go candidate ELF exceeds its byte budget')
    image = elf.ELF(raw)
    if image.kind != 2 or any(program[0] in (2, 3, 7) for program in image.programs):
        raise ValueError('Go candidate must be static ELF64 AMD64 without TLS')
    segments, pages, total, writable = [], set(), 0, 0
    for kind, flags, offset, start, _, size, memory, alignment in image.programs:
        if kind != 1 or not memory:
            continue
        if (flags & ~7 or flags & 3 == 3 or not flags & 4 or size > memory
                or not 0x10000 <= start < 0x10000000 or memory > 0x10000000 - start
                or alignment not in (0, 1) and (alignment & (alignment - 1) or alignment > 2**21
                    or start % alignment != offset % alignment)):
            raise ValueError('Go candidate has unsupported load mapping')
        data = elf._slice(raw, offset, size)
        total += memory
        writable += memory if flags & 2 else 0
        selected_pages = set(range(start // 4096, (start + memory + 4095) // 4096))
        if (pages & selected_pages or total > limits['max_image_bytes']
                or writable > limits['max_image_writable_bytes']):
            raise ValueError('Go candidate mapping overlaps or exceeds its budget')
        pages.update(selected_pages)
        segments.append({'address': start, 'size': memory, 'data': data, 'flags': flags})
    if not segments:
        raise ValueError('Go candidate has no mapped code')
    for section in image.sections:
        if section[1] != 8: elf._slice(raw, section[4], section[5])
        if section[1] in (4, 9, 6, 14, 15, 16) and section[5]:
            raise ValueError('Go candidate contains runtime relocations or initializers')
        if image.section_name(section).startswith(('.debug', '.zdebug')):
            raise ValueError('Go candidate contains unstripped debug metadata')
    selected = images.selected_symbols(image, ('main.reconstructed',))['main.reconstructed']
    if (selected['type'] != 2 or not 1 <= selected['size'] <= 65536 or not any(
            segment['flags'] & 1 and segment['address'] <= selected['address']
            and selected['address'] + selected['size'] <= segment['address'] + segment['size']
            for segment in segments)):
        raise ValueError('Go entry is not a bounded retained function')
    if not any(segment['flags'] & 1 and segment['address'] <= image.entry < segment['address'] + segment['size']
               for segment in segments):
        raise ValueError('Go ELF entry lies outside executable code')
    return {'entry': selected['address'], 'segments': segments, 'sha256': hashlib.sha256(raw).hexdigest()}
