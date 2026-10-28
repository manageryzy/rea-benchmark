"""Strict fixture-local stripping rules for real Rust and Go executables.

Rust's standard allocated GDB auto-loader marker survives GNU strip. It is a
fixed filename, not DWARF/type/address truth, and startup code references it.
Keep that exact marker under this local rule instead of altering the binary or
relaxing the existing shared C/C++ validator.
"""
from capabilities import elf
from pathlib import Path

RUST_GDB_MARKER = b'\x01gdb_load_rust_pretty_printers.py\0'
LOADED_SOURCE = Path(__file__).read_bytes()


def require_loaded():
    if Path(__file__).read_bytes() != LOADED_SOURCE:
        raise RuntimeError('Loaded native ABI image validator changed')


def validate(private_raw, public_raw, language):
    require_loaded()
    if language not in ('rust', 'go'):
        raise ValueError('Unsupported native ABI image language')
    private, public = elf.ELF(private_raw), elf.ELF(public_raw)
    if (private.kind != public.kind or private.entry != public.entry
            or len(private.programs) != len(public.programs) or private.allocated() != public.allocated()):
        raise ValueError('Native stripping changed final loadable contents')
    for left, right in zip(private.programs, public.programs):
        if left == right:
            continue
        # GNU strip can trim Go's trailing zero file padding. Both mappings
        # still have the exact same bytes after ELF zero-fill. No other header
        # field, nonzero padding byte, section or loaded content may change.
        if language != 'go' or left[0] != 1 or any(left[i] != right[i] for i in range(8) if i != 5):
            raise ValueError('Native stripping changed program mappings')
        first = elf._slice(private.raw, left[2], left[5])
        second = elf._slice(public.raw, right[2], right[5])
        size = min(len(first), len(second))
        if first[:size] != second[:size] or any(first[size:]) or any(second[size:]):
            raise ValueError('Native stripping changed zero-filled load memory')
    markers = []
    for section in public.sections:
        name = public.section_name(section)
        if name == '.debug_gdb_scripts':
            if (language != 'rust' or section[1] != 1 or section[2] != 50 or section[5] != len(RUST_GDB_MARKER)
                    or public.bytes_at(section[3] - public.base, section[5]) != RUST_GDB_MARKER):
                raise ValueError('Unrecognized Rust GDB auto-loader metadata')
            markers.append(section)
        elif section[1] == 2 or name.startswith(('.debug', '.zdebug')):
            raise ValueError('Public native image retains symbols or DWARF')
    if len(markers) != (1 if language == 'rust' else 0) or not any(
            private.section_name(s) == '.debug_info' for s in private.sections):
        raise ValueError('Native fixture lacks expected private/debug-marker provenance')
    return private, public


def selected_symbols(image, names):
    """Resolve requested endpoints only; unrelated local-name collisions are legal.

    Large Rust/Go executables can contain identically named local symbols from
    different objects. A requested endpoint must still resolve unambiguously.
    """
    require_loaded()
    names = set(names)
    result = {}
    for section in image.sections:
        if section[1] != 2:
            continue
        if section[9] != elf.SYMBOL.size or section[5] % elf.SYMBOL.size or section[6] >= len(image.sections):
            raise ValueError('Invalid native symbol table')
        string_section = image.sections[section[6]]
        strings = elf._slice(image.raw, string_section[4], string_section[5])
        data = elf._slice(image.raw, section[4], section[5])
        for offset in range(0, len(data), elf.SYMBOL.size):
            name, info, visibility, index, address, size = elf.SYMBOL.unpack_from(data, offset)
            if not name or not index or not address:
                continue
            text = elf._string(strings, name)
            if text not in names:
                continue
            value = {'address': address, 'rva': address - image.base, 'size': size, 'type': info & 15}
            if text in result and result[text] != value:
                raise ValueError('Ambiguous native endpoint: ' + text)
            result[text] = value
    if set(result) != names:
        raise ValueError('Native endpoint symbols missing: ' + ', '.join(sorted(names - set(result))))
    return result
