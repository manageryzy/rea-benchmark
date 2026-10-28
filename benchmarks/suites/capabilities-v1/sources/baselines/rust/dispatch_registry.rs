type Policy = fn(&[u8]) -> u64;

#[inline(never)]
fn add_policy(input: &[u8]) -> u64 {
    input.iter().skip(1).fold(3, |sum, &byte| sum.wrapping_add(u64::from(byte)))
}

#[inline(never)]
fn xor_policy(input: &[u8]) -> u64 {
    input.iter().enumerate().skip(1).fold(11, |value, (index, &byte)| {
        value ^ (u64::from(byte) << (8 * (index % 8)))
    })
}

pub fn evaluate(input: &[u8]) -> u64 {
    let Some(&first) = input.first() else {
        return 0;
    };
    const POLICIES: [Policy; 2] = [add_policy, xor_policy];
    let choice = usize::from(first & 1);
    let selected = std::hint::black_box(POLICIES[choice]);
    selected(input).wrapping_add(choice as u64)
}
