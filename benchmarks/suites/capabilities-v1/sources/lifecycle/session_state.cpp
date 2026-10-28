#include "common.hpp"

namespace {
constexpr std::uint32_t active = 0x1267da43;
struct Session {
    std::uint32_t marker;
    std::uint32_t multiplier;
    std::uint32_t count;
    std::uint32_t checksum;
};
}

extern "C" KEEP std::int32_t family_session(std::uint8_t *storage,
    std::uint32_t operation, std::uint32_t value, std::uint32_t limit,
    std::uint64_t *output, Callback) {
    Session session;
    std::memcpy(&session, storage, sizeof(session));
    if (operation > 4)
        return -1;
    if (operation == 0 && session.marker == active)
        return -5;
    if (operation != 0 && session.marker != active)
        return -2;
    if ((operation == 0 || operation == 2) && (limit == 0 || limit > 31))
        return -1;
    if (operation == 0 || operation == 2)
        session = {active, limit, 0, value};
    else if (operation == 3)
        session = {};
    else {
        if (operation == 1) {
            ++session.count;
            session.checksum = session.checksum * session.multiplier + value + session.count;
        }
        *output = (std::uint64_t{session.checksum} << 32) | session.count;
    }
    std::memcpy(storage, &session, sizeof(session));
    return 0;
}
