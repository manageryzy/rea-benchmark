#include <cstddef>
#include <cstdint>
#include <new>
#include "record.hpp"
#include "fixtures.hpp"

#define KEEP __attribute__((noinline, noipa))
extern "C" std::int64_t recovered_slot(void*, std::uint64_t);

extern "C" KEEP std::int64_t original_callee(void* opaque, std::uint64_t nonce) {
    auto& object = *static_cast<BufferObject*>(opaque);
    const auto length = object.length;
    if (length > object.capacity || length > 32 || object.capacity > 32) return -1;

    const auto* source = object.source ? *object.source : nullptr;
    auto* destination = object.destination ? *object.destination : nullptr;
    if (length && (!source || !destination)) return -2;

    std::uint8_t saved[32];
    for (unsigned i = 0; i < length; ++i) saved[i] = source[i];
    for (unsigned i = 0; i < length; ++i)
        destination[i] = saved[i] ^ static_cast<std::uint8_t>(nonce + 13 * i);
    object.produced = length;
    return 0;
}

template<bool Replacement>
static std::int64_t invoke(std::uint64_t nonce, std::uint8_t* out) {
    std::uint8_t arena[96];
    const auto input = probe_input(nonce, arena);
    const std::uint8_t* source_cell = input.source;
    std::uint8_t* destination_cell = input.destination;

    alignas(16) std::uint8_t storage[192], before[192];
    for (unsigned i = 0; i < 192; ++i)
        storage[i] = static_cast<std::uint8_t>(nonce + 17 * i + 0x93);
    auto* object = ::new (storage + 64) BufferObject;
    object->source = &source_cell;
    object->destination = &destination_cell;
    object->length = input.length;
    object->capacity = input.capacity;
    object->produced = 0xfedc1234U;
    for (unsigned i = 0; i < 192; ++i) before[i] = storage[i];

    const auto status = Replacement ? recovered_slot(object, nonce) : original_callee(object, nonce);
    serialize_probe(out, arena, status, object->produced, before, storage,
                    offsetof(BufferObject, produced));
    // The existing serialized result has a descriptor-preservation word. Extend
    // that witness to the separate pointer cells without publishing addresses.
    if (source_cell != input.source || destination_cell != input.destination)
        emit_word(out + 112, 0);
    return 0;
}

extern "C" KEEP std::int64_t original_caller(std::uint64_t nonce, std::uint8_t* out) {
    return invoke<true>(nonce, out);
}

extern "C" KEEP std::int64_t original_reference(std::uint64_t nonce, std::uint8_t* out) {
    return invoke<false>(nonce, out);
}
