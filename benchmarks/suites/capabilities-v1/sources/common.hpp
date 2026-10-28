#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <algorithm>
#include <iostream>
#include <string>

#if defined(__GNUC__) && !defined(__clang__)
// The first localization condition keeps call boundaries observable. Inlining,
// cloning and LTO belong to separately validated L6 build conditions.
#define KEEP __attribute__((noipa, used))
#elif defined(__clang__)
#define KEEP __attribute__((noinline, used))
#else
#define KEEP
#endif

using Bytes = const std::uint8_t *;
using Result = std::uint64_t;

// Build truth uses these local labels. They are removed from the public file;
// the executable instruction bytes at the labels are retained unchanged.
#define SITE(name) asm volatile(".local " #name "_%=\n" #name "_%=:" ::: "memory")

extern "C" KEEP Result distractor(Bytes data, std::size_t size) {
    Result value = 0;
    for (std::size_t index = 0; index < size; ++index)
        value += data[index];
    return value;
}

extern "C" Result family_root(Bytes, std::size_t);

// Shared observable contract for independent C and original-language recovery.
// Compute the complete message before writing, so input/output overlap is valid.
extern "C" KEEP std::int32_t family_buffer(
    Bytes input, std::size_t size, std::uint8_t *output,
    std::size_t capacity, std::size_t *output_size) {
    if (size > 512) {
        *output_size = 0;
        return -2;
    }
    const auto result = family_root(input, size);
    const auto required = 8 + size;
    *output_size = required;
    if (capacity < required)
        return -1;
    std::array<std::uint8_t, 520> message{};
    for (std::size_t index = 0; index < 8; ++index)
        message[index] = static_cast<std::uint8_t>(result >> (8 * index));
    for (std::size_t index = 0; index < size; ++index)
        message[8 + index] = input[index] ^ message[index % 8];
    std::copy_n(message.begin(), required, output);
    return 0;
}

// The reference protocol is a property of the original executable. Models may
// inspect its bytes but the static-analysis condition never permits execution.
// Only the trusted oracle worker supplies inputs and parses these records.
int main() {
    std::uint64_t id;
    std::size_t size, capacity, output_offset;
    unsigned seed;
    std::string encoded;
    constexpr char digits[] = "0123456789abcdef";
    while (std::cin >> id >> size >> capacity >> output_offset >> seed >> encoded) {
        if (size > 513 || capacity > 520 || output_offset + capacity > 2048 || seed > 255)
            return 2;
        if ((size == 0 && encoded != "-") || (size && encoded.size() != 2 * size))
            return 3;
        std::array<std::uint8_t, 2048> arena;
        for (std::size_t index = 0; index < arena.size(); ++index)
            arena[index] = static_cast<std::uint8_t>(seed + 37 * index);
        for (std::size_t index = 0; index < size; ++index) {
            auto nibble = [](char value) -> unsigned {
                return value <= '9' ? value - '0' : value - 'a' + 10;
            };
            arena[64 + index] = static_cast<std::uint8_t>(
                16 * nibble(encoded[2 * index]) + nibble(encoded[2 * index + 1]));
        }
        std::size_t output_size = static_cast<std::size_t>(-1);
        const auto status = family_buffer(arena.data() + 64, size,
            arena.data() + output_offset, capacity, &output_size);
        std::cout << id << ' ' << status << ' ' << output_size << ' ';
        for (auto value : arena)
            std::cout << digits[value >> 4] << digits[value & 15];
        std::cout << '\n';
    }
    return 0;
}
