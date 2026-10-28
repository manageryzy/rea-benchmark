#include <cstring>

namespace {
class Session {
    std::uint32_t checksum_;
    std::uint32_t count_;
    std::uint32_t remaining_;
    std::uint32_t active_;

public:
    static Session load(const std::uint8_t *storage) {
        Session session;
        std::memcpy(&session, storage, sizeof(session));
        return session;
    }

    void save(std::uint8_t *storage) const {
        std::memcpy(storage, this, sizeof(*this));
    }

    bool active() const { return active_ != 0; }
    bool exhausted() const { return remaining_ == 0; }
    std::uint32_t count() const { return count_; }
    std::uint32_t checksum() const { return checksum_; }
    std::uint64_t value() const { return (std::uint64_t{checksum_} << 32) | count_; }

    void start(std::uint32_t seed, std::uint32_t quota) {
        active_ = 1;
        checksum_ = seed;
        count_ = 0;
        remaining_ = quota;
    }

    void step(std::uint32_t value, std::uint32_t response) {
        checksum_ = (checksum_ * 33) ^ value ^ response;
        ++count_;
        --remaining_;
    }

    void close() { active_ = 0; }
};
}

extern "C" std::int32_t reconstructed(std::uint8_t *storage, std::uint32_t operation,
    std::uint32_t value, std::uint32_t limit, std::uint64_t *output, session_callback callback) {
    auto session = Session::load(storage);
    if (operation > 4) return -1;
    if (operation == 0 && session.active()) return -5;
    if (operation != 0 && !session.active()) return -2;
    if ((operation == 0 || operation == 2) && (limit == 0 || limit > 8)) return -1;
    if (operation == 1 && value > 255) return -1;
    if (operation == 4) {
        *output = session.value();
        return 0;
    }
    if (operation == 1 && session.exhausted()) return -4;
    const auto argument = operation == 3 ? session.checksum() : value;
    const auto index = operation == 0 || operation == 2 ? limit : session.count();
    const auto response = callback(operation, argument, index);
    if (response == UINT64_MAX) return -3;
    if (operation == 0 || operation == 2)
        session.start(value ^ static_cast<std::uint32_t>(response), limit);
    else if (operation == 3)
        session.close();
    else {
        session.step(value, static_cast<std::uint32_t>(response));
        *output = session.value();
    }
    session.save(storage);
    return 0;
}
