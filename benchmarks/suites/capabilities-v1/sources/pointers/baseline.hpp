#pragma once
#include <cstdint>

struct BufferObject {
    std::uint8_t* destination;
    std::uint32_t capacity;
    std::uint32_t produced;
    const std::uint8_t* source;
    std::uint32_t length;
    std::uint32_t marker;
};
static_assert(sizeof(BufferObject) == 32);
