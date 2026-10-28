#include "common.hpp"

#ifndef ABSENT_TARGET
struct Header {
    std::uint16_t length;
    std::uint8_t tag;
    std::uint8_t flags;
};

extern "C" KEEP Result inspect_payload(Bytes data, std::size_t size) {
    SITE(object_decision);
    if (size < sizeof(Header))
        return 0xff00;
    const auto length = static_cast<std::uint16_t>(data[0]) |
                        static_cast<std::uint16_t>(data[1]) << 8;
    if (length > size - sizeof(Header))
        return 0xff01;
    Result result = data[2];
    SITE(object_payload);
    for (std::size_t index = 0; index < length; ++index)
        result = result * 33 + data[sizeof(Header) + index];
    return result ^ data[3];
}

#endif

extern "C" KEEP Result family_root(Bytes data, std::size_t size) {
#ifdef ABSENT_TARGET
    return distractor(data, size);
#else
    return inspect_payload(data, size) + 17;
#endif
}
