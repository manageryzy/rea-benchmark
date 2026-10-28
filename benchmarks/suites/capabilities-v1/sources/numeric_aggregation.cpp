#include "common.hpp"

#ifndef ABSENT_TARGET
extern "C" KEEP Result aggregate_values(Bytes data, std::size_t size) {
    std::array<std::int16_t, 8> values{};
    const auto count = std::min(size / 2, values.size());
    for (std::size_t index = 0; index < count; ++index) {
        const auto bits = static_cast<std::uint16_t>(data[2 * index]) |
                          static_cast<std::uint16_t>(data[2 * index + 1]) << 8;
        values[index] = static_cast<std::int16_t>(bits);
    }
    SITE(numeric_order);
    std::sort(values.begin(), values.begin() + count);
    std::uint32_t total = 0;
    for (std::size_t index = 0; index < count; ++index)
        total += static_cast<std::uint32_t>(values[index]) * (index + 1);
    return total;
}

#endif

extern "C" KEEP Result family_root(Bytes data, std::size_t size) {
#ifdef ABSENT_TARGET
    return distractor(data, size);
#else
    return aggregate_values(data, size) ^ 0x9e3779b9U;
#endif
}
