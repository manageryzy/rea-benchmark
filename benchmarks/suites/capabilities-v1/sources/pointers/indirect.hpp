#pragma once
#include <cstddef>
#include <cstdint>

// A descriptor points to pointer cells, which in turn select the byte ranges.
// Neither the original operation nor a replacement may change those cells.
struct BufferObject {
    std::uint8_t* const* destination;
    std::uint32_t capacity;
    std::uint32_t produced;
    const std::uint8_t* const* source;
    std::uint32_t length;
    std::uint32_t marker;
};

static_assert(sizeof(BufferObject) == 32);
static_assert(offsetof(BufferObject, destination) == 0);
static_assert(offsetof(BufferObject, source) == 16);
static_assert(offsetof(BufferObject, produced) == 12);
