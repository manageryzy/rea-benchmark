#pragma once
#include <cstddef>
#include <cstdint>

struct BufferObject {
    std::uint32_t length;
    const std::uint8_t* const* source;
    std::uint32_t produced;
    std::uint32_t capacity;
    std::uint8_t* const* destination;
};

static_assert(sizeof(BufferObject) == 32);
static_assert(offsetof(BufferObject, source) == 8);
static_assert(offsetof(BufferObject, destination) == 24);
static_assert(offsetof(BufferObject, produced) == 16);
