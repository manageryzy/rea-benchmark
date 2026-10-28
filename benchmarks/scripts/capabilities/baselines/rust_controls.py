"""Readable nonallocating Rust controls for the supplied buffer contract."""

VALUES = {
    'record_codec': '''let decoded = if data.len() >= 4 && data[0] == 0xa7 {
        let count = u32::from(data[1]);
        let value = u32::from(u16::from_le_bytes([data[2], data[3]]));
        u64::from(count.wrapping_mul(value) ^ 0x6d35)
    } else {
        0xffff
    };
    decoded.wrapping_add(u64::from(data.get(4).copied().unwrap_or(0)))''',
    'dispatch_registry': '''if data.is_empty() {
        return 0;
    }
    let choice = data[0] & 1;
    let mut value = if choice == 0 { 3_u64 } else { 11_u64 };
    for (index, &byte) in data.iter().enumerate().skip(1) {
        if choice == 0 {
            value = value.wrapping_add(u64::from(byte));
        } else {
            value ^= u64::from(byte) << (8 * (index % 8));
        }
    }
    value.wrapping_add(u64::from(choice))''',
    'numeric_aggregation': '''let count = core::cmp::min(data.len() / 2, 8);
    let mut values = [0_i16; 8];
    for (index, value) in values[..count].iter_mut().enumerate() {
        *value = i16::from_le_bytes([data[2 * index], data[2 * index + 1]]);
    }
    values[..count].sort_unstable();
    let total = values[..count].iter().enumerate().fold(0_u32, |sum, (index, &value)| {
        sum.wrapping_add((value as u32).wrapping_mul(index as u32 + 1))
    });
    u64::from(total ^ 0x9e37_79b9)''',
    'variable_objects': '''if data.len() < 4 {
        return 0xff00 + 17;
    }
    let length = usize::from(u16::from_le_bytes([data[0], data[1]]));
    if length > data.len() - 4 {
        return 0xff01 + 17;
    }
    let checksum = data[4..4 + length].iter().fold(u64::from(data[2]), |value, &byte| {
        value.wrapping_mul(33).wrapping_add(u64::from(byte))
    });
    (checksum ^ u64::from(data[3])).wrapping_add(17)''',
    'callback_policy': '''data.iter().enumerate().fold(0_u64, |sum, (index, &byte)| {
        sum.wrapping_add((u64::from(byte) + 1).wrapping_mul(index as u64 + 3))
    }).wrapping_add(9)''',
    'session_state': '''let mut count = 0_u32;
    let mut checksum = 0_u32;
    let mut result = 0_u64;
    for &byte in data {
        count = count.wrapping_add(1);
        checksum = checksum.wrapping_mul(17).wrapping_add(u32::from(byte));
        result ^= u64::from(checksum ^ count);
    }
    result''',
}


def solution(family):
    return '''fn evaluate(data: &[u8]) -> u64 {
    ''' + VALUES[family] + '''
}

#[no_mangle]
pub unsafe extern "C" fn reconstructed(
    input: *const u8,
    input_len: usize,
    output: *mut u8,
    output_capacity: usize,
    output_len: *mut usize,
) -> i32 {
    if input_len > 512 {
        output_len.write(0);
        return -2;
    }
    let mut snapshot = [0_u8; 512];
    core::ptr::copy_nonoverlapping(input, snapshot.as_mut_ptr(), input_len);
    let value = evaluate(&snapshot[..input_len]);
    let required = input_len + 8;
    output_len.write(required);
    if output_capacity < required {
        return -1;
    }
    let mut message = [0_u8; 520];
    message[..8].copy_from_slice(&value.to_le_bytes());
    for index in 0..input_len {
        message[8 + index] = snapshot[index] ^ message[index % 8];
    }
    core::ptr::copy_nonoverlapping(message.as_ptr(), output, required);
    0
}
'''
