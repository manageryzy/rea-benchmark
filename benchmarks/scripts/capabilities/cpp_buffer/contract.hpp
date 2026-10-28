#include <cstdint>
#include <cstddef>

extern "C" std::int32_t reconstructed(const std::uint8_t *input, std::size_t input_len,
                                     std::uint8_t *output, std::size_t output_capacity,
                                     std::size_t *output_len);
