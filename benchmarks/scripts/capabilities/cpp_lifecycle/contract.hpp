#include <cstddef>
#include <cstdint>

using session_callback = std::uint64_t (*)(std::uint64_t operation,
                                           std::uint64_t value, std::uint64_t index);
extern "C" std::int32_t reconstructed(std::uint8_t *state, std::uint32_t operation,
                                      std::uint32_t value, std::uint32_t limit,
                                      std::uint64_t *output, session_callback callback);
