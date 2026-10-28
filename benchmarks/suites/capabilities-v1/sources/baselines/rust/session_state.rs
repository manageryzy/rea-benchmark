#[derive(Default)]
struct Session {
    count: u32,
    checksum: u32,
}

impl Session {
    fn update(&mut self, value: u8) -> u64 {
        self.count = self.count.wrapping_add(1);
        self.checksum = self.checksum.wrapping_mul(17).wrapping_add(u32::from(value));
        u64::from(self.checksum ^ self.count)
    }
}

pub fn evaluate(input: &[u8]) -> u64 {
    let mut session = Session::default();
    input.iter().fold(0, |value, &byte| value ^ session.update(byte))
}
