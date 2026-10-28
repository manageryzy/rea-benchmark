type Callback = fn(u8, usize) -> u64;

#[inline(never)]
fn transform_item(value: u8, index: usize) -> u64 {
    (u64::from(value) + 1).wrapping_mul(index as u64 + 3)
}

#[inline(never)]
fn apply_callback(input: &[u8], callback: Callback) -> u64 {
    input.iter().enumerate().fold(0_u64, |sum, (index, &byte)| {
        sum.wrapping_add(callback(byte, index))
    })
}

pub fn evaluate(input: &[u8]) -> u64 {
    apply_callback(input, std::hint::black_box(transform_item as Callback)).wrapping_add(9)
}
