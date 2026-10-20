use std::io::{self, BufRead};

#[no_mangle]
#[inline(never)]
pub unsafe extern "C" fn score_record(p: *const u8, n: usize) -> i64 {
    let b = std::slice::from_raw_parts(p, n);
    if b.len() < 6 { return -1; }
    if b[0] != 0x52 || b[1] != 0x45 { return -2; }
    let count = b[3] as usize;
    if count > 32 || b.len() != 6 + count { return -3; }
    let mut acc = u16::from_le_bytes([b[4], b[5]]) as u32;
    for i in 0..count { acc = acc.wrapping_mul(33) ^ ((b[6+i] as u32) + (i as u32)); }
    match b[2] {
        0 => (acc & 0xffff) as i64,
        1 => if acc & 1 != 0 { ((acc >> 1) & 0xffff) as i64 } else { (acc.wrapping_add(17) & 0xffff) as i64 },
        2 => ((acc ^ 0xa5a5) & 0xffff) as i64,
        _ => -4,
    }
}

fn main() {
    for line in io::stdin().lock().lines() {
        let s = line.unwrap();
        let b: Vec<u8> = if s == "-" { vec![] } else { (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i+2],16).unwrap()).collect() };
        println!("{}", unsafe { score_record(b.as_ptr(), b.len()) });
    }
}
