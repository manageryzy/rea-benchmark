//! Separate compilation preserves real native/C call boundaries without noinline.

#[repr(C)]
pub struct Summary {
    pub weighted: i64,
    pub spread: i64,
    pub signature: i64,
}

fn summarize(a: i64, b: i64, c: i64, d: i64, e: i64, f: i64, g: i64, h: i64) -> Summary {
    let values = [a, b, c, d, e, f, g, h];
    Summary {
        weighted: a + 2 * b - 3 * c + 5 * d + 7 * e - 11 * f + 13 * g - 17 * h,
        spread: values.iter().max().unwrap() - values.iter().min().unwrap(),
        signature: (a ^ h) + (b ^ g) - (c ^ f) + (d ^ e),
    }
}

pub fn wide_native(values: [i64; 8]) -> Summary {
    let [a, b, c, d, e, f, g, h] = values;
    summarize(a, b, c, d, e, f, g, h)
}

pub extern "C" fn wide_c(
    a: i64,
    b: i64,
    c: i64,
    d: i64,
    e: i64,
    f: i64,
    g: i64,
    h: i64,
) -> Summary {
    summarize(a, b, c, d, e, f, g, h)
}
