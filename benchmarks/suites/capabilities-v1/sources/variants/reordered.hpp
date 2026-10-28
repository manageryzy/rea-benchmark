#pragma once
#include <cstdint>

struct CookieBase {
    std::uint64_t cookie;
};

struct StateBase {
    std::uint64_t state;
};

struct PairPayload {
    std::uint32_t second;
    std::int32_t first;
};

union Payload {
    std::int64_t number;
    PairPayload pair;
    Payload() {}
};

struct Envelope : StateBase, CookieBase {
    Payload payload;
    std::uint32_t marker;
    std::uint32_t tag;
};

inline constexpr std::uint32_t number_tag = 0xa7;
inline constexpr std::uint32_t pair_tag = 0x42;
