// Scalar counterfactual: offsets, extents and alignment match the baseline.
#pragma once
#include <cstdint>

struct OriginalItem {
    std::uint16_t key;
    std::uint16_t change;
};

struct OriginalRecord {
    std::uint8_t kind;
    OriginalItem items[2];
    std::uint32_t length;
};

inline void seed_original(OriginalRecord& value, std::uint64_t nonce) {
    value.kind = static_cast<std::uint8_t>(nonce >> 32);
    for (unsigned index = 0; index < 2; ++index) {
        value.items[index].key = static_cast<std::uint16_t>((nonce >> (index * 13)) ^ 0xa71fU);
        value.items[index].change = static_cast<std::uint16_t>((nonce >> (index * 7)) ^ 0x80ffU);
    }
    value.length = static_cast<std::uint32_t>(nonce * 0x9e3779b1U);
}

inline OriginalRecord transform_original(OriginalRecord value, std::uint64_t nonce) {
    value.kind = static_cast<std::uint8_t>(value.kind ^ (nonce >> 9));
    for (unsigned index = 0; index < 2; ++index) {
        const auto change = value.items[index].change;
        value.items[index].key = static_cast<std::uint16_t>(value.items[index].key + (change < 0 ? 257 : 13) + index);
        value.items[index].change = static_cast<std::uint16_t>(change + ((nonce >> (index * 11)) & 127));
    }
    value.length ^= static_cast<std::uint32_t>(nonce >> 16) + 0x6d350103U;
    return value;
}

inline void serialize_original(const OriginalRecord& value, std::uint8_t* out) {
    emit_u64(out, value.kind);
    for (unsigned index = 0; index < 2; ++index) {
        emit_u64(out + 8 + 16 * index, value.items[index].key);
        emit_u64(out + 16 + 16 * index, static_cast<std::uint64_t>(static_cast<std::int64_t>(value.items[index].change)));
    }
    emit_u64(out + 40, value.length);
}

static_assert(sizeof(OriginalRecord) == 16);
static_assert(alignof(OriginalRecord) == 4);
