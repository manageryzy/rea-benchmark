#include "common.hpp"

#ifndef ABSENT_TARGET
extern "C" KEEP Result add_policy(Bytes data, std::size_t size) {
    Result total = 3;
    for (std::size_t index = 1; index < size; ++index)
        total += data[index];
    return total;
}

extern "C" KEEP Result xor_policy(Bytes data, std::size_t size) {
    Result total = 11;
    for (std::size_t index = 1; index < size; ++index)
        total ^= static_cast<Result>(data[index]) << ((index % 8) * 8);
    return total;
}

using Policy = Result (*)(Bytes, std::size_t);
static Policy volatile policy_table[] = {add_policy, xor_policy};

#endif

extern "C" KEEP Result family_root(Bytes data, std::size_t size) {
#ifdef ABSENT_TARGET
    return distractor(data, size);
#else
    SITE(dispatch_decision);
    if (!size)
        return 0;
    const auto choice = data[0] & 1;
    auto policy = policy_table[choice];
    SITE(dispatch_call);
    return policy(data, size) + choice;
#endif
}
