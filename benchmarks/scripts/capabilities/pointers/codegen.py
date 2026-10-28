"""Description-driven memory accesses; model identifiers never enter C++ text."""
from .artifacts import admit


def generate(value):
    admit(value)
    fields = {field['role']: field for field in value['fields']}
    source, destination = fields['source'], fields['destination']
    offsets = {role: field['offset'] for role, field in fields.items()}
    read = lambda role: f'load<std::uint32_t>(object, {offsets[role]})'
    access = source['access'] in ('read', 'read_write') and destination['access'] in ('write', 'read_write')
    def pointer(item):
        offset = str(item['offset'])
        if item['indirections'] == 1:
            return 'load<std::uint8_t*>(object, ' + offset + ')'
        cell = 'load<void*>(object, ' + offset + ')'
        return '(' + cell + ' ? load<std::uint8_t*>(' + cell + ', 0) : nullptr)'
    alias = {'may_overlap': 'true', 'disjoint': '!overlap',
             'identical_or_disjoint': '!overlap || source == destination'}[value['aliasing']]
    text = '''#include <cstdint>
#include <cstddef>
#include "fixtures.hpp"
extern "C" void* memcpy(void*, const void*, std::size_t);
template<class T> static T load(const void* object, std::size_t offset) {
    T result; memcpy(&result, static_cast<const std::uint8_t*>(object) + offset, sizeof(T)); return result;
}
template<class T> static void store(void* object, std::size_t offset, T value) {
    memcpy(static_cast<std::uint8_t*>(object) + offset, &value, sizeof(T));
}
'''
    # One predicate drives the replacement and independently checks an original
    # callee observation. Claims about extents/access/aliasing are exercised, not
    # compared to a private gold JSON description.
    text += f'''static std::int64_t precondition(const std::uint8_t* source, const std::uint8_t* destination,
        std::uint32_t length, std::uint32_t capacity) {{
    if (length > capacity || length > 32 || capacity > 32) return -1;
    if (length && (!source || !destination)) return -2;
    if (!{str(access).lower()}) return -3;
    const auto a = reinterpret_cast<std::uintptr_t>(source), b = reinterpret_cast<std::uintptr_t>(destination);
    const bool overlap = length && a < b + length && b < a + length;
    if (!({alias})) return -4;
    return 0;
}}
extern "C" __attribute__((noinline, noipa)) std::int64_t recovered_slot(void* object, std::uint64_t nonce) {{
    auto* source = {pointer(source)};
    auto* destination = {pointer(destination)};
    auto length = {read(source['extent'])}, capacity = {read(destination['extent'])};
    auto status = precondition(source, destination, length, capacity);
    if (status) return status;
    std::uint8_t saved[32];
    for (unsigned i=0; i<length; ++i) saved[i]=source[i];
    for (unsigned i=0; i<length; ++i) destination[i]=saved[i]^static_cast<std::uint8_t>(nonce+13*i);
    store<std::uint32_t>(object, {offsets['produced']}, length);
    return 0;
}}
extern "C" std::int64_t original_callee(void*, std::uint64_t);
extern "C" __attribute__((noinline, noipa)) std::int64_t generated_bridge(std::uint64_t nonce, std::uint8_t* out) {{
    std::uint8_t arena[96]; const auto input = probe_input(nonce, arena);
    alignas(16) std::uint8_t storage[256], before[192];
    for (unsigned i=0; i<256; ++i) storage[i]=static_cast<std::uint8_t>(nonce+17*i+0x93);
    auto* object=storage+64;
    auto* source_cell=input.source; auto* destination_cell=input.destination;
    store(object, {offsets['source']}, {'input.source' if source['indirections'] == 1 else '&source_cell'});
    store(object, {offsets['destination']}, {'input.destination' if destination['indirections'] == 1 else '&destination_cell'});
    store<std::uint32_t>(object, {offsets['length']}, input.length);
    store<std::uint32_t>(object, {offsets['capacity']}, input.capacity);
    store<std::uint32_t>(object, {offsets['produced']}, 0xfedc1234U);
    for (unsigned i=0; i<192; ++i) before[i]=storage[i];
    const auto length={read(source['extent'])}, capacity={read(destination['extent'])};
    const auto expected=precondition(input.source, input.destination, length, capacity);
    auto status=original_callee(object, nonce);
    if (status != expected || (!status && load<std::uint32_t>(object, {offsets['produced']}) != length)) status=-5;
    serialize_probe(out, arena, status, load<std::uint32_t>(object, {offsets['produced']}), before, storage, {offsets['produced']});
    return 0;
}}
'''
    return text
