#include "common.hpp"

#ifndef ABSENT_TARGET
struct Session {
    std::uint32_t count;
    std::uint32_t checksum;
};

extern "C" KEEP Result update_session(Session *session, std::uint8_t value) {
    SITE(session_update);
    session->count += 1;
    session->checksum = session->checksum * 17 + value;
    return session->checksum ^ session->count;
}

#endif

extern "C" KEEP Result family_root(Bytes data, std::size_t size) {
#ifdef ABSENT_TARGET
    return distractor(data, size);
#else
    Session session{};
    Result result = 0;
    for (std::size_t index = 0; index < size; ++index)
        result ^= update_session(&session, data[index]);
    return result;
#endif
}
