#include <cstdint>
static void emit_u64(std::uint8_t* out, std::uint64_t value) {
    for (unsigned index = 0; index < 8; ++index) out[index] = static_cast<std::uint8_t>(value >> (8 * index));
}
#include "original.hpp"
#if ORIGINAL_MS_ABI
#define ORIGINAL_ABI __attribute__((ms_abi))
#else
#define ORIGINAL_ABI __attribute__((sysv_abi))
#endif
extern "C" __attribute__((noinline, noipa)) OriginalRecord ORIGINAL_ABI recovered_slot(OriginalRecord value, std::uint64_t nonce) {
    return transform_original(value, nonce);
}
