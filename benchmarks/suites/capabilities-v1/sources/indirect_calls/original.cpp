#include <bit>
#include <cstddef>
#include <cstdint>
#include <new>
#include "record.hpp"
#include "fixtures.hpp"

#define KEEP __attribute__((noinline, noipa))
extern "C" std::int64_t recovered_slot(void*, std::uint64_t);

struct CallbackContext {
    std::uint64_t bias;
    std::uint64_t salt;
    std::uint64_t state;
    std::uint32_t calls;
    std::uint32_t marker;
};
static_assert(sizeof(Service) <= 128 && alignof(Service) <= 16);
static_assert(sizeof(CallbackContext) == 32);

static std::uint64_t rotate(std::uint64_t value, unsigned shift) {
    return shift ? (value << shift) | (value >> (64 - shift)) : value;
}

std::uint64_t CookieBase::cookie_view() const { return cookie; }
std::uint64_t PaddingBase::padding_view() const { return padding; }
std::uint64_t DispatchBase::preview(std::uint64_t value) { return value ^ 0x13579bdfULL; }
std::uint64_t DispatchBase::spare(std::uint64_t value) { return value + 0x2468ace0ULL; }
std::uint64_t DispatchBase::apply_a(std::uint64_t value) { return ~value; }
std::uint64_t DispatchBase::apply_b(std::uint64_t value) { return value ^ 0x9327ULL; }

extern "C" KEEP std::uint64_t original_leaf_0(void* opaque, std::uint64_t nonce) {
    auto& service = *static_cast<Service*>(opaque);
    service.state = rotate(service.state ^ nonce, static_cast<unsigned>(nonce & 63)) + service.cookie;
    return service.state ^ 0x6a09e667f3bcc909ULL;
}

extern "C" KEEP std::uint64_t original_leaf_1(void* opaque, std::uint64_t nonce) {
    auto& service = *static_cast<Service*>(opaque);
    service.state += (nonce * 0x9e3779b97f4a7c15ULL) ^ service.cookie;
    return service.state + rotate(service.cookie, static_cast<unsigned>((nonce >> 1) & 63));
}

KEEP std::uint64_t Service::apply_a(std::uint64_t nonce) { return original_leaf_0(this, nonce); }
KEEP std::uint64_t Service::apply_b(std::uint64_t nonce) { return original_leaf_1(this, nonce); }

static KEEP std::uint64_t callback_a(void* opaque, std::uint64_t value) {
    auto& context = *static_cast<CallbackContext*>(opaque);
    ++context.calls;
    context.state ^= rotate(value + context.salt, 17);
    return context.state + context.bias;
}

static KEEP std::uint64_t callback_b(void* opaque, std::uint64_t value) {
    auto& context = *static_cast<CallbackContext*>(opaque);
    context.calls += 3;
    context.state += value ^ context.salt;
    return context.state ^ context.bias;
}

static Service* service_at(std::uint8_t* storage, unsigned index) {
    return reinterpret_cast<Service*>(storage + 64 + 192 * index);
}
static const Service* service_at(const std::uint8_t* storage, unsigned index) {
    return reinterpret_cast<const Service*>(storage + 64 + 192 * index);
}
static CallbackContext* context_at(std::uint8_t* storage, unsigned index) {
    return reinterpret_cast<CallbackContext*>(storage + 448 + 128 * index);
}
static const CallbackContext* context_at(const std::uint8_t* storage, unsigned index) {
    return reinterpret_cast<const CallbackContext*>(storage + 448 + 128 * index);
}

extern "C" KEEP void original_fixture_initialize(std::uint64_t nonce, std::uint8_t* storage,
                                                 FixtureEndpoints* endpoints) {
    for (unsigned i = 0; i < fixture_storage_bytes; ++i)
        storage[i] = static_cast<std::uint8_t>(nonce + 31 * i + 0xb7);
    for (unsigned i = 0; i < 2; ++i) {
        auto* service = ::new (service_at(storage, i)) Service;
        service->cookie = nonce * 0xd1342543de82ef95ULL + (i + 1) * 0x123456789abcdefULL;
        service->state = nonce ^ ((i + 1) * 0x9362a7d50bc14ef8ULL);
        service->marker = 0x93d5072a6bc41ef8ULL;
        auto* context = ::new (context_at(storage, i)) CallbackContext;
        context->bias = nonce + (i + 1) * 0x1122334455667788ULL;
        context->salt = nonce ^ ((i + 1) * 0x9e3779b97f4a7c15ULL);
        context->state = ~nonce + i * 0x23456789abcdef01ULL;
        context->calls = 0;
        context->marker = 0x5d7923b1U;
        endpoints->receivers[i] = static_cast<DispatchBase*>(service);
        endpoints->contexts[i] = context;
    }
    endpoints->callbacks[0] = callback_a;
    endpoints->callbacks[1] = callback_b;
    const unsigned operation = static_cast<unsigned>(nonce & 1);
    switch ((nonce >> 2) % 5) {
    case 1: endpoints->receivers[operation] = nullptr; break;
    case 2: endpoints->callbacks[operation] = nullptr; break;
    case 3: endpoints->contexts[operation] = nullptr; break;
    default: break;
    }
}

extern "C" KEEP std::int64_t original_callee(void* opaque, std::uint64_t nonce) {
    auto& root = *static_cast<CallRoot*>(opaque);
    const unsigned operation = static_cast<unsigned>(nonce & 1);
    auto* receiver = static_cast<DispatchBase*>(root.receivers[receiver_fields[operation]]);
    const auto callback = root.callbacks[callback_fields[operation]];
    auto* context = root.contexts[context_fields[operation]];
    if (!receiver || !callback || !context)
        return -9;
    const auto value = operation == 0 ? receiver->apply_a(nonce) : receiver->apply_b(nonce);
    return std::bit_cast<std::int64_t>(callback(context, value));
}

extern "C" KEEP void original_fixture_observe(const std::uint8_t* before, const std::uint8_t* storage,
                                              const CallRoot* root_before, const CallRoot* root,
                                              std::int64_t result, std::uint8_t* output) {
    bool root_preserved = true;
    auto* root_bytes = reinterpret_cast<const std::uint8_t*>(root);
    auto* before_bytes = reinterpret_cast<const std::uint8_t*>(root_before);
    for (unsigned i = 0; i < sizeof(CallRoot); ++i)
        root_preserved = root_preserved && root_bytes[i] == before_bytes[i];
    bool protected_storage = true;
    for (unsigned offset = 0; offset < fixture_storage_bytes; ++offset) {
        bool mutable_byte = false;
        for (unsigned i = 0; i < 2; ++i) {
            const auto state = reinterpret_cast<const std::uint8_t*>(&service_at(storage, i)->state) - storage;
            const auto context_state = reinterpret_cast<const std::uint8_t*>(&context_at(storage, i)->state) - storage;
            const auto calls = reinterpret_cast<const std::uint8_t*>(&context_at(storage, i)->calls) - storage;
            mutable_byte = mutable_byte || (state <= offset && offset < state + 8)
                         || (context_state <= offset && offset < context_state + 8)
                         || (calls <= offset && offset < calls + 4);
        }
        if (!mutable_byte && before[offset] != storage[offset])
            protected_storage = false;
    }
    call_emit_word(output, std::bit_cast<std::uint64_t>(result));
    call_emit_word(output + 8, service_at(storage, 0)->state);
    call_emit_word(output + 16, service_at(storage, 1)->state);
    call_emit_word(output + 24, context_at(storage, 0)->state);
    call_emit_word(output + 32, context_at(storage, 1)->state);
    call_emit_word(output + 40, context_at(storage, 0)->calls
                   | (static_cast<std::uint64_t>(context_at(storage, 1)->calls) << 32));
    call_emit_word(output + 48, root_preserved);
    call_emit_word(output + 56, protected_storage);
}

template<bool Replacement>
static std::int64_t invoke(std::uint64_t nonce, std::uint8_t* output) {
    alignas(16) std::uint8_t storage[fixture_storage_bytes], before[fixture_storage_bytes];
    FixtureEndpoints endpoints;
    original_fixture_initialize(nonce, storage, &endpoints);
    CallRoot root{};
    for (unsigned i = 0; i < 2; ++i) {
        root.receivers[receiver_fields[i]] = endpoints.receivers[i];
        root.callbacks[callback_fields[i]] = endpoints.callbacks[i];
        root.contexts[context_fields[i]] = endpoints.contexts[i];
    }
    root.seal = nonce ^ 0xa7531d9e62bc04f8ULL;
    const auto root_before = root;
    for (unsigned i = 0; i < fixture_storage_bytes; ++i)
        before[i] = storage[i];
    const auto result = Replacement ? recovered_slot(&root, nonce) : original_callee(&root, nonce);
    original_fixture_observe(before, storage, &root_before, &root, result, output);
    return 0;
}

extern "C" KEEP std::int64_t original_caller(std::uint64_t nonce, std::uint8_t* output) {
    return invoke<true>(nonce, output);
}
extern "C" KEEP std::int64_t original_reference(std::uint64_t nonce, std::uint8_t* output) {
    return invoke<false>(nonce, output);
}
