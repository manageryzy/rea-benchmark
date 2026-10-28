#pragma once
#include <cstddef>
#include <cstdint>

// Shared invocation premises, not the recovered descriptor layout.
struct ProbeInput {
    std::uint8_t* source;
    std::uint8_t* destination;
    std::uint32_t length;
    std::uint32_t capacity;
};

inline ProbeInput probe_input(std::uint64_t nonce, std::uint8_t* arena) {
    for (unsigned i = 0; i < 96; ++i)
        arena[i] = static_cast<std::uint8_t>(nonce + 31 * i + 0xa5);
    ProbeInput input{arena + 8, arena + 48, 8, 16};
    switch (nonce % 12) {
    case 1: input = {arena + 8, arena + 14, 12, 16}; break;
    case 2: input = {arena + 10, arena + 6, 12, 16}; break;
    case 3: input = {arena + 8, arena + 8, 12, 12}; break;
    case 4: input = {nullptr, nullptr, 0, 0}; break;
    case 5: input.length = 8; input.capacity = 4; break;
    case 6: input.length = input.capacity = 16; break;
    case 7: input.length = 3; input.capacity = 9; break;
    case 8: input.length = 3; input.capacity = 0; break;
    case 9: input.source = nullptr; break;
    case 10: input.destination = nullptr; break;
    case 11: input.length = input.capacity = 1; break;
    }
    return input;
}

inline void emit_word(std::uint8_t* out, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i) out[i] = static_cast<std::uint8_t>(value >> (8 * i));
}

inline void serialize_probe(std::uint8_t* out, const std::uint8_t* arena,
                            std::int64_t status, std::uint32_t produced,
                            const std::uint8_t* before, const std::uint8_t* after,
                            std::size_t produced_offset) {
    for (unsigned i = 0; i < 96; ++i) out[i] = arena[i];
    emit_word(out + 96, static_cast<std::uint64_t>(status));
    emit_word(out + 104, produced);
    bool descriptor_preserved = true, guards_preserved = true;
    for (unsigned i = 0; i < 192; ++i) {
        if (64 + produced_offset <= i && i < 68 + produced_offset) continue;
        if (before[i] != after[i]) {
            if (i < 64 || i >= 128) guards_preserved = false;
            else descriptor_preserved = false;
        }
    }
    emit_word(out + 112, descriptor_preserved);
    emit_word(out + 120, guards_preserved);
}
