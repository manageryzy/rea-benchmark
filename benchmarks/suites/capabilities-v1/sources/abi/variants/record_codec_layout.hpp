// Layout counterfactual: semantic scalar types and record extents are unchanged.
#pragma once
#include <cstdint>

struct OriginalRecord {
    std::uint32_t weight;
    std::int16_t delta;
    std::uint8_t flags;
    std::uint8_t reserved[4];
    std::uint32_t values[3];
};

inline void seed_original(OriginalRecord& value, std::uint64_t nonce) {
    value.delta = static_cast<std::int16_t>(nonce ^ (nonce >> 17));
    value.weight = static_cast<std::uint32_t>(nonce * 0x9e3779b1U);
    value.flags = static_cast<std::uint8_t>(nonce >> 32);
    for (unsigned index = 0; index < 3; ++index)
        value.values[index] = static_cast<std::uint32_t>((nonce >> (index * 9)) + 0x80ff0103U * (index + 1));
}

inline OriginalRecord transform_original(OriginalRecord value, std::uint64_t nonce) {
    const auto old_delta = value.delta;
    const auto old_weight = value.weight;
    std::uint32_t saved[3] = {value.values[0], value.values[1], value.values[2]};
    value.delta = static_cast<std::int16_t>(old_delta < 0 ? old_delta - (nonce & 63) : old_delta + (nonce & 31));
    value.weight = (old_weight ^ static_cast<std::uint32_t>(nonce)) + (old_delta < 0 ? 0x10203U : 0xa1b2c3d4U);
    value.flags = static_cast<std::uint8_t>((value.flags + ((nonce >> 8) & 255)) ^ (old_weight >> 24));
    for (unsigned index = 0; index < 3; ++index)
        value.values[index] = (saved[(index + 1) % 3] + static_cast<std::uint32_t>(old_delta) * (index + 3)) ^
                              (old_weight >> (index + 1)) ^ static_cast<std::uint32_t>(nonce >> (index * 7));
    return value;
}

inline void serialize_original(const OriginalRecord& value, std::uint8_t* out) {
    emit_u64(out, static_cast<std::uint64_t>(static_cast<std::int64_t>(value.delta)));
    emit_u64(out + 8, value.weight);
    emit_u64(out + 16, value.flags);
    for (unsigned index = 0; index < 3; ++index)
        emit_u64(out + 24 + 8 * index, value.values[index]);
}

static_assert(sizeof(OriginalRecord) == 24);
static_assert(alignof(OriginalRecord) == 4);
