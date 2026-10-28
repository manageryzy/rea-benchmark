#![no_std]

include!("interface.rs");

extern "Rust" {
    #[link_name = "native_transform"]
    fn original_native(packet: Packet, nonce: u64) -> Reply;
}

// The calibration slot delegates to the actual original, rather than carrying
// a second implementation of the expected arithmetic.
#[no_mangle]
#[inline(never)]
pub fn replacement_slot(packet: Packet, nonce: u64) -> Reply {
    unsafe { original_native(packet, nonce) }
}
