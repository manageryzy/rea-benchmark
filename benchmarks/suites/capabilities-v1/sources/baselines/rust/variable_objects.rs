struct Header {
    length: usize,
    tag: u8,
    flags: u8,
}

fn inspect_payload(input: &[u8]) -> u64 {
    if input.len() < 4 {
        return 0xff00;
    }
    let header = Header {
        length: usize::from(u16::from_le_bytes([input[0], input[1]])),
        tag: input[2],
        flags: input[3],
    };
    if header.length > input.len() - 4 {
        return 0xff01;
    }
    let checksum = input[4..4 + header.length].iter().fold(u64::from(header.tag), |value, &byte| {
        value.wrapping_mul(33).wrapping_add(u64::from(byte))
    });
    checksum ^ u64::from(header.flags)
}

pub fn evaluate(input: &[u8]) -> u64 {
    inspect_payload(input).wrapping_add(17)
}
