#pragma once
#include <bit>
#include <cstdint>

inline constexpr std::uint64_t operation_salt = 0x93d50b7a6241ef8cULL;

[[gnu::always_inline]] inline std::uint64_t mix_word(std::uint64_t value, std::uint64_t salt) {
    const auto rotated = std::rotl(value ^ salt, 7);
    return rotated * 33 + 109;
}

extern "C" std::uint64_t cross_unit_mix(std::uint64_t value);
