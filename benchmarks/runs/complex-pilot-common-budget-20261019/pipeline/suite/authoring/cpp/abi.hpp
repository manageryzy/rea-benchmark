#pragma once

#include <cstdint>

struct Summary {
    std::int64_t weighted;
    std::int64_t spread;
    std::int64_t signature;
};

extern "C" Summary wide_sysv(std::int64_t a, std::int64_t b, std::int64_t c, std::int64_t d,
                             std::int64_t e, std::int64_t f, std::int64_t g, std::int64_t h);

extern "C" __attribute__((ms_abi)) Summary wide_ms(std::int64_t a, std::int64_t b, std::int64_t c,
                                                   std::int64_t d, std::int64_t e, std::int64_t f,
                                                   std::int64_t g, std::int64_t h);
