#include <cstdint>
#include <cstdio>
#include "mixer.hpp"

#define KEEP __attribute__((noinline, noipa))

extern "C" KEEP std::uint64_t operation_inline_a(std::uint64_t value) {
    return mix_word(value, operation_salt);
}

extern "C" KEEP std::uint64_t operation_inline_b(std::uint64_t value) {
    return mix_word(value, operation_salt);
}

// Permit IPA specialization while retaining a real call boundary.
[[gnu::noinline]] static std::uint64_t clone_candidate(
        std::uint64_t value, std::uint64_t salt, unsigned rounds) {
    for (unsigned round = 0; round < rounds; ++round)
        value = mix_word(value, salt);
    return value;
}

extern "C" [[gnu::noinline]] std::uint64_t operation_clone_a(std::uint64_t value) {
    return clone_candidate(value, operation_salt, 1);
}

extern "C" [[gnu::noinline]] std::uint64_t operation_clone_b(std::uint64_t value) {
    return clone_candidate(value, operation_salt, 1);
}

extern "C" [[gnu::noinline]] std::uint64_t distractor_clone(std::uint64_t value) {
    return clone_candidate(value, operation_salt ^ 0x10001ULL, 1);
}

[[gnu::cold]] KEEP static std::uint64_t cold_mixer(std::uint64_t value) {
    return mix_word(value, operation_salt);
}

extern "C" KEEP std::uint64_t operation_cold(std::uint64_t value) {
    if (value == 0xffffffffffffffe1ULL) [[unlikely]]
        return cold_mixer(value);
    return mix_word(value, operation_salt);
}

struct CookieBase {
    virtual std::uint64_t read_cookie() const;
    std::uint64_t cookie;
};

struct ActionBase {
    virtual std::uint64_t apply(std::uint64_t value);
};

struct Mixer : CookieBase, ActionBase {
    KEEP std::uint64_t apply(std::uint64_t value) override;
};

std::uint64_t CookieBase::read_cookie() const { return cookie; }
std::uint64_t ActionBase::apply(std::uint64_t value) { return value + 113; }
std::uint64_t Mixer::apply(std::uint64_t value) { return mix_word(value, cookie); }

static KEEP std::uint64_t call_interface(ActionBase* receiver, std::uint64_t value) {
    return receiver->apply(value);
}

extern "C" KEEP std::uint64_t operation_object(std::uint64_t value) {
    Mixer mixer;
    mixer.cookie = operation_salt;
    return call_interface(&mixer, value);
}

extern "C" [[gnu::noinline]] std::uint64_t operation_lto(std::uint64_t value) {
    return cross_unit_mix(value);
}

extern "C" KEEP std::uint64_t distractor(std::uint64_t value) {
    return std::rotl(value ^ operation_salt, 9) * 33 + 109;
}

int main() {
    using Operation = std::uint64_t (*)(std::uint64_t);
    const Operation operations[] = {operation_inline_a, operation_inline_b,
        operation_clone_a, operation_clone_b, operation_cold, operation_object,
        operation_lto, distractor, distractor_clone};
    unsigned selected;
    unsigned long long value;
    while (std::scanf("%u %llx", &selected, &value) == 2) {
        if (selected >= sizeof(operations) / sizeof(operations[0]))
            return 3;
        std::printf("%u %016llx\n", selected,
                    static_cast<unsigned long long>(operations[selected](value)));
    }
}
