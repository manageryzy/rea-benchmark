#include <cstdint>
#include <cstddef>
#include <new>

static void emit_u64(std::uint8_t* out, std::uint64_t value) {
    for (unsigned index = 0; index < 8; ++index)
        out[index] = static_cast<std::uint8_t>(value >> (index * 8));
}

#include "original.hpp"

#define KEEP __attribute__((noinline, noipa))
#if ORIGINAL_MS_ABI
#define ORIGINAL_ABI __attribute__((ms_abi))
#else
#define ORIGINAL_ABI __attribute__((sysv_abi))
#endif

extern "C" OriginalRecord ORIGINAL_ABI recovered_slot(OriginalRecord, std::uint64_t);

extern "C" KEEP OriginalRecord ORIGINAL_ABI original_callee(OriginalRecord value, std::uint64_t nonce) {
    // A concrete alignment/home-slot witness, retained in this ABI condition.
    if (reinterpret_cast<std::uintptr_t>(__builtin_frame_address(0)) & 15) __builtin_trap();
    asm volatile("" : "+m"(nonce));
    return transform_original(value, nonce);
}

static OriginalRecord input_record(std::uint64_t nonce) {
    OriginalRecord input;
    auto* raw = reinterpret_cast<std::uint8_t*>(&input);
    for (std::size_t index = 0; index < sizeof(input); ++index)
        raw[index] = static_cast<std::uint8_t>((nonce >> (8 * (index % 8))) + 37 * index + 0x51);
    seed_original(input, nonce);
    return input;
}

// This caller is compiled once and retained verbatim as an object. A generated
// replacement must satisfy its original ABI and original record definition.
extern "C" KEEP std::int64_t original_caller(std::uint64_t nonce, std::uint8_t* out) {
    alignas(16) volatile std::uint8_t guarded[256];
    for (unsigned index = 0; index < 256; ++index)
        guarded[index] = static_cast<std::uint8_t>(nonce + 19 * index + 0x73);
    const OriginalRecord input = input_record(nonce);
    register std::uint64_t keep_bx asm("rbx") = nonce ^ 0x1234567101;
    register std::uint64_t keep_12 asm("r12") = nonce ^ 0x2345678202;
    register std::uint64_t keep_13 asm("r13") = nonce ^ 0x3456789303;
    register std::uint64_t keep_14 asm("r14") = nonce ^ 0x456789a404;
    register std::uint64_t keep_15 asm("r15") = nonce ^ 0x56789ab505;
    asm volatile("" : "+r"(keep_bx), "+r"(keep_12), "+r"(keep_13), "+r"(keep_14), "+r"(keep_15));
    // C++17 guaranteed elision makes the original return slot itself guarded.
    auto* value = ::new (const_cast<std::uint8_t*>(guarded + 64)) OriginalRecord(recovered_slot(input, nonce));
    asm volatile("" : "+r"(keep_bx), "+r"(keep_12), "+r"(keep_13), "+r"(keep_14), "+r"(keep_15));
    if (keep_bx != (nonce ^ 0x1234567101) || keep_12 != (nonce ^ 0x2345678202) ||
        keep_13 != (nonce ^ 0x3456789303) || keep_14 != (nonce ^ 0x456789a404) || keep_15 != (nonce ^ 0x56789ab505))
        return 2;
    serialize_original(*value, out);
    for (unsigned index = 0; index < 256; ++index)
        if ((index < 64 || index >= 64 + sizeof(OriginalRecord)) &&
            guarded[index] != static_cast<std::uint8_t>(nonce + 19 * index + 0x73))
            return 1;
    return 0;
}

extern "C" KEEP std::int64_t original_reference(std::uint64_t nonce, std::uint8_t* out) {
    const auto value = original_callee(input_record(nonce), nonce);
    serialize_original(value, out);
    return 0;
}
