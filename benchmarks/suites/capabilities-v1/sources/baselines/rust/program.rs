use std::error::Error;
use std::io::{self, Read, Write};

mod family;

#[no_mangle]
#[inline(never)]
pub unsafe extern "C" fn family_buffer(
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
    // Finish all reads before writing, including when buffers overlap.
    let mut snapshot = [0_u8; 512];
    std::ptr::copy_nonoverlapping(input, snapshot.as_mut_ptr(), input_len);
    let value = family::evaluate(&snapshot[..input_len]);
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
    std::ptr::copy_nonoverlapping(message.as_ptr(), output, required);
    0
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut records = String::new();
    io::stdin().read_to_string(&mut records)?;
    let mut sink = io::BufWriter::new(io::stdout().lock());
    for line in records.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        if fields.len() != 6 {
            return Err("invalid reference record".into());
        }
        let id = fields[0].parse::<u64>()?;
        let size = fields[1].parse::<usize>()?;
        let capacity = fields[2].parse::<usize>()?;
        let offset = fields[3].parse::<usize>()?;
        let seed = fields[4].parse::<u8>()?;
        let encoded = fields[5];
        if size > 513 || capacity > 520 || offset.checked_add(capacity).is_none_or(|end| end > 2048)
            || (size == 0 && encoded != "-") || (size != 0 && encoded.len() != 2 * size) {
            return Err("reference record exceeds its bounds".into());
        }
        let mut arena = [0_u8; 2048];
        for (index, byte) in arena.iter_mut().enumerate() {
            *byte = seed.wrapping_add((37 * index) as u8);
        }
        for index in 0..size {
            arena[64 + index] = u8::from_str_radix(&encoded[2 * index..2 * index + 2], 16)?;
        }
        let mut output_len = usize::MAX;
        let status = unsafe {
            family_buffer(arena.as_ptr().add(64), size, arena.as_mut_ptr().add(offset), capacity, &mut output_len)
        };
        write!(sink, "{id} {status} {output_len} ")?;
        for byte in arena {
            write!(sink, "{byte:02x}")?;
        }
        writeln!(sink)?;
    }
    Ok(())
}
