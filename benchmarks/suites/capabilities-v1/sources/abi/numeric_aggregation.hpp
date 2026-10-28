#pragma once
#include <cstdint>

struct OriginalRecord {
    std::int16_t delta;
    std::uint32_t weight;
};

inline void seed_original(OriginalRecord& value, std::uint64_t nonce) {
    value.delta = static_cast<std::int16_t>(nonce ^ (nonce >> 17));
    value.weight = static_cast<std::uint32_t>(nonce * 0x9e3779b1U);
}

inline OriginalRecord transform_original(OriginalRecord value, std::uint64_t nonce) {
    const auto delta = value.delta;
    value.delta = static_cast<std::int16_t>(delta < 0 ? delta - (nonce & 63) : delta + (nonce & 31));
    value.weight = (value.weight ^ static_cast<std::uint32_t>(nonce)) + (delta < 0 ? 0x10203U : 0xa1b2c3d4U);
    return value;
}

inline void serialize_original(const OriginalRecord& value, std::uint8_t* out) {
    emit_u64(out, static_cast<std::uint64_t>(static_cast<std::int64_t>(value.delta)));
    emit_u64(out + 8, value.weight);
}
