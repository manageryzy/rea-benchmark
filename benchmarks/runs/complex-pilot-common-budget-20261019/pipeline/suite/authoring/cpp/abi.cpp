#include "abi.hpp"

#include <algorithm>
#include <array>

namespace {
Summary summarize(std::int64_t a, std::int64_t b, std::int64_t c, std::int64_t d, std::int64_t e,
                  std::int64_t f, std::int64_t g, std::int64_t h) {
    const std::array values{a, b, c, d, e, f, g, h};
    const auto [low, high] = std::minmax_element(values.begin(), values.end());
    return {a + 2 * b - 3 * c + 5 * d + 7 * e - 11 * f + 13 * g - 17 * h, *high - *low,
            (a ^ h) + (b ^ g) - (c ^ f) + (d ^ e)};
}
} // namespace

extern "C" Summary wide_sysv(std::int64_t a, std::int64_t b, std::int64_t c, std::int64_t d,
                             std::int64_t e, std::int64_t f, std::int64_t g, std::int64_t h) {
    return summarize(a, b, c, d, e, f, g, h);
}

extern "C" __attribute__((ms_abi)) Summary wide_ms(std::int64_t a, std::int64_t b, std::int64_t c,
                                                   std::int64_t d, std::int64_t e, std::int64_t f,
                                                   std::int64_t g, std::int64_t h) {
    return summarize(a, b, c, d, e, f, g, h);
}
