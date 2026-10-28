#include <cstdint>
#include <cstddef>
#include <new>
#include "record.hpp"
#include "fixtures.hpp"

#define KEEP __attribute__((noinline, noipa))
extern "C" std::int64_t recovered_slot(void*, std::uint64_t);

extern "C" KEEP std::int64_t original_callee(void* opaque, std::uint64_t nonce) {
    auto& object = *static_cast<BufferObject*>(opaque);
    const auto length = object.length;
    if (length > object.capacity || length > 32 || object.capacity > 32) return -1;
    if (length && (!object.source || !object.destination)) return -2;
    std::uint8_t saved[32];
    for (unsigned i = 0; i < length; ++i) saved[i] = object.source[i];
    for (unsigned i = 0; i < length; ++i)
        object.destination[i] = saved[i] ^ static_cast<std::uint8_t>(nonce + 13 * i);
    object.produced = length;
    return 0;
}

template<bool Replacement>
static std::int64_t invoke(std::uint64_t nonce, std::uint8_t* out) {
    std::uint8_t arena[96];
    const auto input = probe_input(nonce, arena);
    alignas(16) std::uint8_t storage[192], before[192];
    for (unsigned i = 0; i < 192; ++i) storage[i] = static_cast<std::uint8_t>(nonce + 17 * i + 0x93);
    auto* object = ::new (storage + 64) BufferObject;
    object->source = input.source;
    object->destination = input.destination;
    object->length = input.length;
    object->capacity = input.capacity;
    object->produced = 0xfedc1234U;
    object->marker = 0x9327d50bU;
    for (unsigned i = 0; i < 192; ++i) before[i] = storage[i];
    const auto status = Replacement ? recovered_slot(object, nonce) : original_callee(object, nonce);
    serialize_probe(out, arena, status, object->produced, before, storage, offsetof(BufferObject, produced));
    return 0;
}

extern "C" KEEP std::int64_t original_caller(std::uint64_t nonce, std::uint8_t* out) {
    return invoke<true>(nonce, out);
}
extern "C" KEEP std::int64_t original_reference(std::uint64_t nonce, std::uint8_t* out) {
    return invoke<false>(nonce, out);
}
