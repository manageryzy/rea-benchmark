#include "common.hpp"

#ifndef ABSENT_TARGET
using Callback = Result (*)(std::uint8_t, std::size_t);

extern "C" KEEP Result transform_item(std::uint8_t value, std::size_t index) {
    return (static_cast<Result>(value) + 1) * (index + 3);
}

extern "C" KEEP Result apply_callback(Bytes data, std::size_t size, Callback callback) {
    Result result = 0;
    for (std::size_t index = 0; index < size; ++index) {
        SITE(callback_call);
        result += callback(data[index], index);
    }
    return result;
}

#endif

extern "C" KEEP Result family_root(Bytes data, std::size_t size) {
#ifdef ABSENT_TARGET
    return distractor(data, size);
#else
    return apply_callback(data, size, transform_item) + 9;
#endif
}
