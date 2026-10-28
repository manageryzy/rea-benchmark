#include <cstring>

namespace {
class Session {
    std::uint32_t active_;
    std::uint32_t checksum_;
    std::uint32_t count_;
    std::uint32_t multiplier_;

public:
    static Session load(const std::uint8_t *storage) {
        Session session;
        std::memcpy(&session, storage, sizeof(session));
        return session;
    }

    void save(std::uint8_t *storage) const { std::memcpy(storage, this, sizeof(*this)); }
    bool active() const { return active_ != 0; }
    std::uint64_t value() const { return (std::uint64_t{checksum_} << 32) | count_; }

    void start(std::uint32_t seed, std::uint32_t multiplier) {
        active_ = 1;
        checksum_ = seed;
        count_ = 0;
        multiplier_ = multiplier;
    }

    void step(std::uint32_t input) {
        ++count_;
        checksum_ = checksum_ * multiplier_ + input + count_;
    }

    void close() { active_ = 0; }
};
}

extern "C" std::int32_t reconstructed(std::uint8_t *storage, std::uint32_t operation,
    std::uint32_t value, std::uint32_t limit, std::uint64_t *output, session_callback) {
    auto session = Session::load(storage);
    if (operation > 4) return -1;
    if (operation == 0 && session.active()) return -5;
    if (operation != 0 && !session.active()) return -2;
    if ((operation == 0 || operation == 2) && (limit == 0 || limit > 31)) return -1;
    if (operation == 0 || operation == 2)
        session.start(value, limit);
    else if (operation == 3)
        session.close();
    else {
        if (operation == 1) session.step(value);
        *output = session.value();
    }
    session.save(storage);
    return 0;
}
