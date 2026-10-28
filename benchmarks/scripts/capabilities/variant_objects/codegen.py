"""Actual tag, union and subobject accesses generated from an admitted description."""
from .artifacts import admit


def generate(value):
    admit(value)
    offsets = {row['role']: row['offset'] for row in value['subobjects']}
    payload = value['payload']
    alternatives = {row['role']: row for row in payload['alternatives']}
    number, pair = alternatives['number'], alternatives['pair']
    cookie, state, tag = offsets['cookie'], offsets['state'], value['tag_offset']
    n = payload['offset'] + number['offset']
    a, b = payload['offset'] + pair['first_offset'], payload['offset'] + pair['second_offset']
    return f'''#include <cstdint>
#include <cstddef>
#include "fixtures.hpp"
extern "C" void* memcpy(void*, const void*, std::size_t);
template<class T> static T claim_load(const void* object, std::size_t offset) {{
    T value; memcpy(&value, static_cast<const std::uint8_t*>(object)+offset, sizeof(T)); return value;
}}
template<class T> static void claim_store(void* object, std::size_t offset, T value) {{
    memcpy(static_cast<std::uint8_t*>(object)+offset, &value, sizeof(T));
}}
static std::uint64_t claim_rotate(std::uint64_t value, unsigned shift) {{
    return shift ? (value << shift) | (value >> (64-shift)) : value;
}}
extern "C" __attribute__((noinline,noipa)) std::int64_t recovered_slot(void* object, std::uint64_t nonce) {{
    const auto tag=claim_load<std::uint32_t>(object,{tag});
    const auto cookie=claim_load<std::uint64_t>(object,{cookie});
    const auto state=claim_load<std::uint64_t>(object,{state});
    if (tag=={number['tag']}U) {{
        const auto bits=claim_load<std::uint64_t>(object,{n});
        claim_store(object,{state},claim_rotate(state^bits,static_cast<unsigned>(nonce&63))+cookie);
        claim_store(object,{n},bits^cookie^nonce);
        return 0;
    }}
    if (tag=={pair['tag']}U) {{
        const auto first=claim_load<std::uint32_t>(object,{a});
        const auto second=claim_load<std::uint32_t>(object,{b});
        claim_store(object,{state},state+(((static_cast<std::uint64_t>(first)<<32)|second)^cookie));
        claim_store<std::uint32_t>(object,{a},first+static_cast<std::uint32_t>(nonce));
        claim_store<std::uint32_t>(object,{b},second^static_cast<std::uint32_t>(cookie));
        return 0;
    }}
    return -7;
}}
extern "C" std::int64_t original_callee(void*,std::uint64_t);
extern "C" __attribute__((noinline,noipa)) std::int64_t generated_bridge(std::uint64_t nonce,std::uint8_t* output) {{
    const auto input=variant_input(nonce);
    alignas(16) std::uint8_t storage[192],before[192];
    for (unsigned i=0;i<192;++i) storage[i]=static_cast<std::uint8_t>(nonce+17*i+0x93);
    auto* object=storage+64;
    claim_store(object,{cookie},input.cookie);
    claim_store(object,{state},input.state);
    const std::uint32_t tag=input.kind==0 ? {number['tag']}U : input.kind==1 ? {pair['tag']}U : 0xffffffffU;
    claim_store(object,{tag},tag);
    if (input.kind==0) claim_store(object,{n},input.number_bits);
    else if (input.kind==1) {{
        claim_store<std::uint32_t>(object,{a},std::bit_cast<std::uint32_t>(input.first));
        claim_store(object,{b},input.second);
    }}
    for (unsigned i=0;i<192;++i) before[i]=storage[i];
    const auto status=original_callee(object,nonce);
    std::uint64_t active_value=0;
    if (input.kind==0) active_value=claim_load<std::uint64_t>(object,{n});
    else if (input.kind==1) active_value=(static_cast<std::uint64_t>(claim_load<std::uint32_t>(object,{a}))<<32)
                                      |claim_load<std::uint32_t>(object,{b});
    bool descriptor_preserved=true,guards_preserved=true;
    for (unsigned i=0;i<192;++i) {{
        const bool state_byte=64+{state}<=i && i<72+{state};
        const bool number_byte=input.kind==0 && 64+{n}<=i && i<72+{n};
        const bool pair_byte=input.kind==1 && ((64+{a}<=i && i<68+{a})||(64+{b}<=i && i<68+{b}));
        if (!state_byte && !number_byte && !pair_byte && before[i]!=storage[i]) {{
            if (i<64 || i>=128) guards_preserved=false;
            else descriptor_preserved=false;
        }}
    }}
    variant_emit_word(output,static_cast<std::uint64_t>(status));
    variant_emit_word(output+8,claim_load<std::uint64_t>(object,{state}));
    variant_emit_word(output+16,claim_load<std::uint64_t>(object,{cookie}));
    variant_emit_word(output+24,active_value);
    variant_emit_word(output+32,input.kind);
    variant_emit_word(output+40,claim_load<std::uint32_t>(object,{tag})==tag);
    variant_emit_word(output+48,descriptor_preserved);
    variant_emit_word(output+56,guards_preserved);
    return 0;
}}
'''
