use native_fixture::{ffi_transform, native_caller, native_transform, Packet};
use std::io::{self, BufRead};

fn main() {
    for line in io::stdin().lock().lines() {
        let line = line.expect("input line");
        let fields: Vec<_> = line.split_whitespace().collect();
        assert_eq!(fields.len(), 5);
        let selected: u32 = fields[0].parse().expect("selector");
        let values: Vec<_> = fields[1..].iter().map(|field|
            u64::from_str_radix(field, 16).expect("hexadecimal uint64")
        ).collect();
        let packet = Packet { first: values[1], second: values[2], third: values[3] };
        let reply = match selected {
            0 => native_transform(packet, values[0]),
            1 => native_caller(packet, values[0]),
            2 => ffi_transform(packet, values[0]),
            _ => panic!("unknown selector"),
        };
        println!("{} {:016x} {:016x} {:016x}", selected,
                 reply.first, reply.second, reply.third);
    }
}
