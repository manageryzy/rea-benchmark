#![no_std]

include!("interface.rs");

extern "Rust" {
    #[link_name = "replacement_slot"]
    fn replacement(packet: Packet, nonce: u64) -> Reply;
}

// The record layout is supplied. This function deliberately retains Rust's
// native calling ABI; repr(C) on a record does not change a function's ABI.
#[no_mangle]
#[inline(never)]
pub fn native_transform(packet: Packet, nonce: u64) -> Reply {
    let first = (packet.first ^ nonce)
        .rotate_left(11)
        .wrapping_add(packet.second.wrapping_mul(17));
    let second = packet.second.wrapping_add(0x7a6d_5039_12c4_b8ef)
        .rotate_left(23) ^ packet.third;
    let third = packet.third.wrapping_add(nonce.wrapping_mul(257))
        ^ first.wrapping_add(second).rotate_left(5);
    Reply { first, second, third }
}

#[no_mangle]
#[inline(never)]
pub fn native_caller(packet: Packet, nonce: u64) -> Reply {
    // The separately linked slot can be replaced without editing this caller
    // or native_transform. Its declaration uses the Rust ABI too.
    let mut reply = unsafe { replacement(packet, nonce) };
    reply.third ^= 0x4c83_9e17_20b6_d5fa;
    reply
}

// This is a separately labelled stable-FFI control, not a T7 result.
#[no_mangle]
#[inline(never)]
pub extern "C" fn ffi_transform(packet: Packet, nonce: u64) -> Reply {
    native_transform(packet, nonce)
}
