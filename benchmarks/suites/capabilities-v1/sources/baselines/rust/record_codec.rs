fn decode_record(input: &[u8]) -> u64 {
    if input.len() < 4 || input[0] != 0xa7 {
        return 0xffff;
    }
    let count = u32::from(input[1]);
    let value = u32::from(u16::from_le_bytes([input[2], input[3]]));
    u64::from(count.wrapping_mul(value) ^ 0x6d35)
}

pub fn evaluate(input: &[u8]) -> u64 {
    let trailing = input.get(4).copied().unwrap_or(0);
    decode_record(input).wrapping_add(u64::from(trailing))
}
