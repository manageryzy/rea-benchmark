#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <iostream>

#define KEEP __attribute__((noipa, used))
using Callback = std::uint64_t (*)(std::uint64_t, std::uint64_t, std::uint64_t);

extern "C" std::int32_t family_session(std::uint8_t *, std::uint32_t,
    std::uint32_t, std::uint32_t, std::uint64_t *, Callback);

namespace protocol {
std::uint64_t response;
std::array<std::array<std::uint64_t, 3>, 8> events;
std::size_t calls;

std::uint64_t callback(std::uint64_t operation, std::uint64_t value, std::uint64_t index) {
    if (calls >= events.size())
        std::exit(4);
    events[calls++] = {operation, value, index};
    return response;
}
}

// Private reference frontend. The same loaded code/data survives stripping.
// Each input record starts a fresh scenario or advances its existing instances.
int main() {
    alignas(8) std::array<std::uint8_t, 512> arena{};
    std::uint64_t id, fresh, instance, operation, value, limit, seed;
    constexpr char digits[] = "0123456789abcdef";
    while (std::cin >> id >> fresh >> instance >> operation >> value >> limit
                    >> protocol::response >> seed) {
        if (instance > 1 || operation > UINT32_MAX || value > UINT32_MAX
            || limit > UINT32_MAX || seed > 255 || fresh > 1)
            return 2;
        if (fresh) {
            for (std::size_t index = 0; index < arena.size(); ++index)
                arena[index] = static_cast<std::uint8_t>(seed + 37 * index);
            std::fill_n(arena.data() + 32, 64, 0);
            std::fill_n(arena.data() + 160, 64, 0);
        }
        for (std::size_t index = 0; index < 8; ++index)
            arena[256 + index] = static_cast<std::uint8_t>(seed + 19 * index);
        std::uint64_t output;
        std::memcpy(&output, arena.data() + 256, sizeof(output));
        protocol::calls = 0;
        const auto status = family_session(arena.data() + (instance ? 160 : 32),
            static_cast<std::uint32_t>(operation), static_cast<std::uint32_t>(value),
            static_cast<std::uint32_t>(limit), &output, protocol::callback);
        std::memcpy(arena.data() + 256, &output, sizeof(output));
        std::cout << id << ' ' << status << ' ';
        for (auto byte : arena)
            std::cout << digits[byte >> 4] << digits[byte & 15];
        std::cout << ' ' << protocol::calls;
        for (std::size_t index = 0; index < protocol::calls; ++index)
            for (auto argument : protocol::events[index])
                std::cout << ' ' << argument;
        std::cout << '\n';
    }
}
