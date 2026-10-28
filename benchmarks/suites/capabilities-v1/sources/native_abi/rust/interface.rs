#[repr(C)]
#[derive(Clone, Copy)]
pub struct Packet {
    pub first: u64,
    pub second: u64,
    pub third: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Reply {
    pub first: u64,
    pub second: u64,
    pub third: u64,
}
