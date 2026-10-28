#pragma once
#include <cstddef>
#include <cstdint>

using KnownCallback = std::uint64_t (*)(void*, std::uint64_t);

// Complete supplied layout. T6 measures call relationships, not these offsets.
struct CallRoot {
    void* receivers[2];
    KnownCallback callbacks[2];
    void* contexts[2];
    std::uint64_t seal;
};
static_assert(sizeof(CallRoot) == 56 && alignof(CallRoot) == 8);
static_assert(offsetof(CallRoot, callbacks) == 16 && offsetof(CallRoot, contexts) == 32);

// Trusted calibration/probe API; receiver and context contents remain opaque.
struct FixtureEndpoints {
    void* receivers[2];
    KnownCallback callbacks[2];
    void* contexts[2];
};
inline constexpr unsigned fixture_storage_bytes = 768;

extern "C" void original_fixture_initialize(std::uint64_t, std::uint8_t*, FixtureEndpoints*);
extern "C" void original_fixture_observe(const std::uint8_t*, const std::uint8_t*,
                                         const CallRoot*, const CallRoot*, std::int64_t,
                                         std::uint8_t*);
extern "C" std::uint64_t original_leaf_0(void*, std::uint64_t);
extern "C" std::uint64_t original_leaf_1(void*, std::uint64_t);

inline void call_emit_word(std::uint8_t* output, std::uint64_t value) {
    for (unsigned i = 0; i < 8; ++i)
        output[i] = static_cast<std::uint8_t>(value >> (8 * i));
}
