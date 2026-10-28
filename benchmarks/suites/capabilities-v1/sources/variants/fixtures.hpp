#pragma once
#include <bit>
#include <cstdint>

struct VariantInput {
    unsigned kind;
    std::uint64_t number_bits;
    std::int32_t first;
    std::uint32_t second;
    std::uint64_t cookie;
    std::uint64_t state;
};

inline VariantInput variant_input(std::uint64_t nonce) {
    VariantInput input{
        static_cast<unsigned>(nonce % 6),
        nonce ^ 0xfedcba9876543210ULL,
        std::bit_cast<std::int32_t>(static_cast<std::uint32_t>(nonce) ^ 0x80000000U),
        static_cast<std::uint32_t>(nonce >> 32) ^ 0xfedcba98U,
        nonce * 0x9e3779b97f4a7c15ULL + 0x123456789abcdef0ULL,
        nonce ^ 0xd5b72639012aef84ULL
    };
    switch (input.kind) {
    case 0: input.kind = 0; input.number_bits = ~nonce; break;
    case 1: input.kind = 1; break;
    case 2: input.kind = 2; break;
    case 3: input.kind = 0; input.number_bits = 0x8000000000000000ULL; break;
    case 4: input.kind = 1; input.first = -7; input.second = 0x12345678U; break;
    case 5: input.kind = 0; input.number_bits = 42; break;
    }
    return input;
}

inline void variant_emit_word(std::uint8_t* output, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i)
        output[i] = static_cast<std::uint8_t>(value >> (8 * i));
}
