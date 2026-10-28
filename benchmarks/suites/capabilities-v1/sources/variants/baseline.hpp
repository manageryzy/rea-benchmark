#pragma once
#include <cstdint>

struct CookieBase {
    std::uint64_t cookie;
};

struct StateBase {
    std::uint64_t state;
};

struct PairPayload {
    std::int32_t first;
    std::uint32_t second;
};

union Payload {
    std::int64_t number;
    PairPayload pair;
    Payload() {}
};

struct Envelope : CookieBase, StateBase {
    std::uint32_t tag;
    std::uint32_t marker;
    Payload payload;
};

inline constexpr std::uint32_t number_tag = 0x31;
inline constexpr std::uint32_t pair_tag = 0x85;
