pub fn evaluate(input: &[u8]) -> u64 {
    let mut values: Vec<i16> = input.chunks_exact(2).take(8)
        .map(|pair| i16::from_le_bytes([pair[0], pair[1]]))
        .collect();
    values.sort_unstable();
    let sum = values.iter().enumerate().fold(0_u32, |sum, (index, &value)| {
        sum.wrapping_add((value as u32).wrapping_mul(index as u32 + 1))
    });
    u64::from(sum ^ 0x9e37_79b9)
}
