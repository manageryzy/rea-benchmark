#include "common.hpp"

namespace {
constexpr std::uint32_t active = 0x73431a9b;
struct Session {
    std::uint32_t marker;
    std::uint32_t remaining;
    std::uint32_t count;
    std::uint32_t checksum;
};
}

extern "C" KEEP std::int32_t family_session(std::uint8_t *storage,
    std::uint32_t operation, std::uint32_t value, std::uint32_t limit,
    std::uint64_t *output, Callback callback) {
    Session session;
    std::memcpy(&session, storage, sizeof(session));
    if (operation > 4)
        return -1;
    if (operation == 0 && session.marker == active)
        return -5;
    if (operation != 0 && session.marker != active)
        return -2;
    if ((operation == 0 || operation == 2) && (limit == 0 || limit > 8))
        return -1;
    if (operation == 1 && value > 255)
        return -1;
    if (operation == 4) {
        *output = (std::uint64_t{session.checksum} << 32) | session.count;
        return 0;
    }
    if (operation == 1 && session.remaining == 0)
        return -4;
    const auto argument = operation == 3 ? session.checksum : value;
    const auto index = operation == 0 || operation == 2 ? limit : session.count;
    const auto reply = callback(operation, argument, index);
    if (reply == UINT64_MAX)
        return -3;  // A failing callback commits no state or output.
    if (operation == 0 || operation == 2)
        session = {active, limit, 0, value ^ static_cast<std::uint32_t>(reply)};
    else if (operation == 3)
        session = {};
    else {
        --session.remaining;
        ++session.count;
        session.checksum = (session.checksum * 33) ^ value ^ static_cast<std::uint32_t>(reply);
        *output = (std::uint64_t{session.checksum} << 32) | session.count;
    }
    std::memcpy(storage, &session, sizeof(session));
    return 0;
}
