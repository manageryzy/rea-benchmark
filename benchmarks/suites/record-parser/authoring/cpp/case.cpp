#include <cstdint>
#include <cstdio>
#include <iostream>
#include <span>
#include <string>
#include <vector>

extern "C" __attribute__((noinline)) std::int64_t score_record(const std::uint8_t *p, std::size_t n) {
    std::span<const std::uint8_t> b(p, n);
    if (b.size() < 6) return -1;
    if (b[0] != 0x52 || b[1] != 0x45) return -2;
    std::uint32_t count = b[3];
    if (count > 32 || b.size() != 6 + count) return -3;
    std::uint32_t acc = b[4] | (std::uint32_t(b[5]) << 8);
    for (std::uint32_t i = 0; i < count; ++i) acc = (acc * 33u) ^ (std::uint32_t(b[6 + i]) + i);
    switch (b[2]) {
        case 0: return acc & 0xffffu;
        case 1: return (acc & 1u) ? ((acc >> 1) & 0xffffu) : ((acc + 17u) & 0xffffu);
        case 2: return (acc ^ 0xa5a5u) & 0xffffu;
        default: return -4;
    }
}

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::vector<std::uint8_t> bytes;
        if (line != "-") for (std::size_t i = 0; i + 1 < line.size(); i += 2) bytes.push_back(std::stoul(line.substr(i, 2), nullptr, 16));
        std::printf("%lld\n", static_cast<long long>(score_record(bytes.data(), bytes.size())));
    }
}
