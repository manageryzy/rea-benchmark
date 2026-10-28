#include <cstddef>

extern "C" void* memcpy(void* destination, const void* source, std::size_t length) {
    auto* out = static_cast<unsigned char*>(destination);
    const auto* in = static_cast<const unsigned char*>(source);
    for (std::size_t index = 0; index < length; ++index) out[index] = in[index];
    return destination;
}

extern "C" void* memmove(void* destination, const void* source, std::size_t length) {
    auto* out = static_cast<unsigned char*>(destination);
    const auto* in = static_cast<const unsigned char*>(source);
    if (out < in) {
        for (std::size_t index = 0; index < length; ++index) out[index] = in[index];
    } else {
        for (std::size_t index = length; index; --index) out[index - 1] = in[index - 1];
    }
    return destination;
}

extern "C" void* memset(void* destination, int value, std::size_t length) {
    auto* out = static_cast<unsigned char*>(destination);
    for (std::size_t index = 0; index < length; ++index) out[index] = static_cast<unsigned char>(value);
    return destination;
}
