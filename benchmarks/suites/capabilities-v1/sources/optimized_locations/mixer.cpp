#include "mixer.hpp"

// The caller is in another translation unit. LTO may erase this boundary.
extern "C" std::uint64_t cross_unit_mix(std::uint64_t value) {
    return mix_word(value, operation_salt);
}
