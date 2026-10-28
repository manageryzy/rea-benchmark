#pragma once
#include <cstdint>

struct BufferObject {
    std::uint32_t length;
    std::uint32_t marker;
    const std::uint8_t* source;
    std::uint32_t produced;
    std::uint32_t capacity;
    std::uint8_t* destination;
};
static_assert(sizeof(BufferObject) == 32);
