"""Use each submitted relationship in real virtual, direct-leaf and callback calls."""
from .artifacts import admit


def generate(value):
    admit(value)
    operations = sorted(value['operations'], key=lambda row: row['operation'])
    def values(name):
        return ','.join(str(row[name]) for row in operations)
    return f'''#include <bit>
#include <cstddef>
#include <cstdint>
#include "fixtures.hpp"
extern "C" void* memcpy(void*, const void*, std::size_t);
static constexpr unsigned receiver_slots[2]={{{values('receiver_slot')}}};
static constexpr unsigned callback_slots[2]={{{values('callback_slot')}}};
static constexpr unsigned context_slots[2]={{{values('context_slot')}}};
static constexpr unsigned virtual_slots[2]={{{values('virtual_slot')}}};
static constexpr std::ptrdiff_t this_adjustments[2]={{{values('this_adjustment')}}};

extern "C" __attribute__((noinline,noipa)) std::int64_t recovered_slot(void* opaque, std::uint64_t nonce) {{
    auto& root=*static_cast<CallRoot*>(opaque);
    const unsigned operation=static_cast<unsigned>(nonce&1);
    auto* receiver=root.receivers[receiver_slots[operation]];
    const auto callback=root.callbacks[callback_slots[operation]];
    auto* context=root.contexts[context_slots[operation]];
    if (!receiver || !callback || !context) return -9;
    std::uint64_t result;
    if (nonce&2) {{
        auto* owner=reinterpret_cast<void*>(reinterpret_cast<std::uintptr_t>(receiver)
                                           +this_adjustments[operation]);
        result=operation==0 ? original_leaf_0(owner,nonce) : original_leaf_1(owner,nonce);
    }} else {{
        using Method=std::uint64_t (*)(void*,std::uint64_t);
        Method* table;
        memcpy(&table,receiver,sizeof(table));
        Method method;
        memcpy(&method,table+virtual_slots[operation],sizeof(method));
        result=method(receiver,nonce);
    }}
    return std::bit_cast<std::int64_t>(callback(context,result));
}}

extern "C" std::int64_t original_callee(void*,std::uint64_t);
extern "C" __attribute__((noinline,noipa)) std::int64_t generated_bridge(std::uint64_t nonce,std::uint8_t* output) {{
    alignas(16) std::uint8_t storage[fixture_storage_bytes],before[fixture_storage_bytes];
    FixtureEndpoints endpoints;
    original_fixture_initialize(nonce,storage,&endpoints);
    CallRoot root{{}};
    for (unsigned i=0;i<2;++i) {{
        root.receivers[receiver_slots[i]]=endpoints.receivers[i];
        root.callbacks[callback_slots[i]]=endpoints.callbacks[i];
        root.contexts[context_slots[i]]=endpoints.contexts[i];
    }}
    root.seal=nonce^0xa7531d9e62bc04f8ULL;
    const auto root_before=root;
    for (unsigned i=0;i<fixture_storage_bytes;++i) before[i]=storage[i];
    const auto result=original_callee(&root,nonce);
    original_fixture_observe(before,storage,&root_before,&root,result,output);
    return 0;
}}
'''
