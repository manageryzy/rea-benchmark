#include "common.hpp"

#ifndef ABSENT_TARGET
static volatile std::uint32_t codec_salt = 0x6d35;

extern "C" KEEP Result decode_item(Bytes data, std::size_t size) {
    SITE(codec_decision);
    if (size < 4 || data[0] != 0xa7)
        return 0xffff;
    const auto count = static_cast<std::uint32_t>(data[1]);
    const auto value = static_cast<std::uint32_t>(data[2]) |
                       static_cast<std::uint32_t>(data[3]) << 8;
    SITE(codec_update);
    return (count * value) ^ codec_salt;
}

#endif

extern "C" KEEP Result family_root(Bytes data, std::size_t size) {
#ifdef ABSENT_TARGET
    return distractor(data, size);
#else
    return decode_item(data, size) + (size > 4 ? data[4] : 0);
#endif
}
