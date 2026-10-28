#include <bit>
#include <cstddef>
#include <cstdint>
#include <new>
#include "record.hpp"
#include "fixtures.hpp"

#define KEEP __attribute__((noinline, noipa))
extern "C" std::int64_t recovered_slot(void*, std::uint64_t);

static std::uint64_t rotate(std::uint64_t value, unsigned shift) {
    return shift ? (value << shift) | (value >> (64 - shift)) : value;
}

extern "C" KEEP std::int64_t original_callee(void* opaque, std::uint64_t nonce) {
    auto& object = *static_cast<Envelope*>(opaque);
    if (object.tag == number_tag) {
        const auto bits = std::bit_cast<std::uint64_t>(object.payload.number);
        object.state = rotate(object.state ^ bits, static_cast<unsigned>(nonce & 63)) + object.cookie;
        object.payload.number = std::bit_cast<std::int64_t>(bits ^ object.cookie ^ nonce);
        return 0;
    }
    if (object.tag == pair_tag) {
        const auto first = std::bit_cast<std::uint32_t>(object.payload.pair.first);
        const auto packed = (static_cast<std::uint64_t>(first) << 32) | object.payload.pair.second;
        object.state += packed ^ object.cookie;
        object.payload.pair.first = std::bit_cast<std::int32_t>(first + static_cast<std::uint32_t>(nonce));
        object.payload.pair.second ^= static_cast<std::uint32_t>(object.cookie);
        return 0;
    }
    return -7;
}

template<bool Replacement>
static std::int64_t invoke(std::uint64_t nonce, std::uint8_t* output) {
    const auto input = variant_input(nonce);
    alignas(16) std::uint8_t storage[192], before[192];
    for (unsigned i = 0; i < 192; ++i)
        storage[i] = static_cast<std::uint8_t>(nonce + 17 * i + 0x93);
    auto* object = ::new (storage + 64) Envelope;
    object->cookie = input.cookie;
    object->state = input.state;
    object->marker = 0x9327d50bU;
    if (input.kind == 0) {
        object->tag = number_tag;
        ::new (&object->payload.number) std::int64_t(std::bit_cast<std::int64_t>(input.number_bits));
    } else if (input.kind == 1) {
        object->tag = pair_tag;
        auto* pair = ::new (&object->payload.pair) PairPayload;
        pair->first = input.first;
        pair->second = input.second;
    } else {
        object->tag = 0xffffffffU;
    }
    for (unsigned i = 0; i < 192; ++i) before[i] = storage[i];
    const auto initial_tag = object->tag;
    const auto status = Replacement ? recovered_slot(object, nonce) : original_callee(object, nonce);

    // Only active payload values are observed. Inactive union storage and
    // unspecified padding never become expected semantic output.
    std::uint64_t active_value = 0;
    if (input.kind == 0)
        active_value = std::bit_cast<std::uint64_t>(object->payload.number);
    else if (input.kind == 1)
        active_value = (static_cast<std::uint64_t>(std::bit_cast<std::uint32_t>(object->payload.pair.first)) << 32)
                       | object->payload.pair.second;

    const auto base = reinterpret_cast<std::uintptr_t>(object);
    const auto state_offset = reinterpret_cast<std::uintptr_t>(&object->state) - base;
    const auto payload_offset = reinterpret_cast<std::uintptr_t>(&object->payload) - base;
    bool descriptor_preserved = true, guards_preserved = true;
    for (unsigned i = 0; i < 192; ++i) {
        const bool state_byte = 64 + state_offset <= i && i < 72 + state_offset;
        const bool payload_byte = input.kind < 2 && 64 + payload_offset <= i && i < 72 + payload_offset;
        if (!state_byte && !payload_byte && before[i] != storage[i]) {
            if (i < 64 || i >= 128) guards_preserved = false;
            else descriptor_preserved = false;
        }
    }
    variant_emit_word(output, static_cast<std::uint64_t>(status));
    variant_emit_word(output + 8, object->state);
    variant_emit_word(output + 16, object->cookie);
    variant_emit_word(output + 24, active_value);
    variant_emit_word(output + 32, input.kind);
    variant_emit_word(output + 40, object->tag == initial_tag);
    variant_emit_word(output + 48, descriptor_preserved);
    variant_emit_word(output + 56, guards_preserved);
    return 0;
}

extern "C" KEEP std::int64_t original_caller(std::uint64_t nonce, std::uint8_t* output) {
    return invoke<true>(nonce, output);
}

extern "C" KEEP std::int64_t original_reference(std::uint64_t nonce, std::uint8_t* output) {
    return invoke<false>(nonce, output);
}
