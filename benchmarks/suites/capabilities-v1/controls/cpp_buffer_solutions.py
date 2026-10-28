"""Readable original-language controls; never supplied to reconstruction agents."""

LOGIC = {
    'record_codec': r'''
std::uint64_t compute_value(std::span<const std::uint8_t> data) {
    std::uint64_t result = 65535;
    if (data.size() >= 4 && data[0] == 0xa7) {
        const auto value = static_cast<std::uint32_t>(data[2]) |
                           (static_cast<std::uint32_t>(data[3]) << 8);
        result = (data[1] * value) ^ 0x6d35U;
    }
    return result + (data.size() > 4 ? data[4] : 0);
}
''',
    'dispatch_registry': r'''
std::uint64_t compute_value(std::span<const std::uint8_t> data) {
    if (data.empty()) return 0;
    const auto choice = data.front() & 1;
    std::uint64_t result = choice ? 11 : 3;
    for (std::size_t index = 1; index < data.size(); ++index) {
        if (choice) result ^= static_cast<std::uint64_t>(data[index]) << ((index % 8) * 8);
        else result += data[index];
    }
    return result + choice;
}
''',
    'numeric_aggregation': r'''
std::uint64_t compute_value(std::span<const std::uint8_t> data) {
    std::array<std::int32_t, 8> values{};
    const auto count = std::min(data.size() / 2, values.size());
    for (std::size_t index = 0; index < count; ++index) {
        const auto bits = static_cast<std::uint32_t>(data[2 * index]) |
                          (static_cast<std::uint32_t>(data[2 * index + 1]) << 8);
        values[index] = bits >= 32768 ? static_cast<std::int32_t>(bits) - 65536 : static_cast<std::int32_t>(bits);
    }
    std::sort(values.begin(), values.begin() + count);
    std::uint32_t result = 0;
    for (std::size_t index = 0; index < count; ++index)
        result += static_cast<std::uint32_t>(values[index]) * static_cast<std::uint32_t>(index + 1);
    return result ^ 0x9e3779b9U;
}
''',
    'variable_objects': r'''
std::uint64_t compute_value(std::span<const std::uint8_t> data) {
    if (data.size() < 4) return 0xff00U + 17;
    const auto length = static_cast<std::uint32_t>(data[0]) |
                        (static_cast<std::uint32_t>(data[1]) << 8);
    if (length > data.size() - 4) return 0xff01U + 17;
    std::uint64_t result = data[2];
    for (const auto byte : data.subspan(4, length)) result = result * 33 + byte;
    return (result ^ data[3]) + 17;
}
''',
    'callback_policy': r'''
std::uint64_t compute_value(std::span<const std::uint8_t> data) {
    const auto transform = [](std::uint8_t byte, std::size_t index) {
        return (static_cast<std::uint64_t>(byte) + 1) * (index + 3);
    };
    std::uint64_t result = 0;
    for (std::size_t index = 0; index < data.size(); ++index) result += transform(data[index], index);
    return result + 9;
}
''',
    'session_state': r'''
struct Session {
    std::uint32_t count = 0;
    std::uint32_t checksum = 0;
    std::uint32_t step(std::uint8_t byte) {
        ++count;
        checksum = checksum * 17U + byte;
        return checksum ^ count;
    }
};
std::uint64_t compute_value(std::span<const std::uint8_t> data) {
    Session session;
    std::uint64_t result = 0;
    for (const auto byte : data) result ^= session.step(byte);
    return result;
}
'''}

BUFFER = r'''
extern "C" std::int32_t reconstructed(const std::uint8_t *input, std::size_t input_len,
                                     std::uint8_t *output, std::size_t capacity,
                                     std::size_t *output_len) {
    if (input_len > 512) {
        *output_len = 0;
        return -2;
    }
    const auto result = compute_value({input, input_len});
    const auto required = 8 + input_len;
    *output_len = required;
    if (capacity < required) return -1;

    // Assemble before copying so every permitted overlap has snapshot semantics.
    std::array<std::uint8_t, 520> message{};
    for (std::size_t index = 0; index < 8; ++index)
        message[index] = static_cast<std::uint8_t>(result >> (8 * index));
    for (std::size_t index = 0; index < input_len; ++index)
        message[8 + index] = input[index] ^ message[index % 8];
    std::copy_n(message.begin(), required, output);
    return 0;
}
'''


def solution(family):
    return '#include <algorithm>\n#include <array>\n#include <cstddef>\n#include <cstdint>\n#include <span>\n\n' + LOGIC[family] + BUFFER
