#pragma once
#include <cstdint>

struct CookieBase {
    virtual std::uint64_t cookie_view() const;
    std::uint64_t cookie;
};

struct PaddingBase {
    virtual std::uint64_t padding_view() const;
    std::uint64_t padding = 0x4c1d7ab39620ef85ULL;
};

struct DispatchBase {
    virtual std::uint64_t preview(std::uint64_t);
    virtual std::uint64_t apply_a(std::uint64_t);
    virtual std::uint64_t spare(std::uint64_t);
    virtual std::uint64_t apply_b(std::uint64_t);
};

struct Service : CookieBase, DispatchBase {
    std::uint64_t state;
    std::uint64_t marker;
    std::uint64_t apply_a(std::uint64_t) override;
    std::uint64_t apply_b(std::uint64_t) override;
};

inline constexpr unsigned receiver_fields[2] = {0, 1};
inline constexpr unsigned callback_fields[2] = {1, 0};
inline constexpr unsigned context_fields[2] = {0, 1};
