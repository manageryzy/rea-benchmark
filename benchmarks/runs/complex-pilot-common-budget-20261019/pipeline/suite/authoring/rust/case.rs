use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap};

fn signed_word(data: &[u8], offset: usize) -> i64 {
    i16::from_le_bytes([data[offset], data[offset + 1]]) as i64
}

fn joined(items: impl IntoIterator<Item = String>) -> String {
    let result = items.into_iter().collect::<Vec<_>>().join(",");
    if result.is_empty() {
        "-".into()
    } else {
        result
    }
}

fn normalized_character(value: u8) -> Option<char> {
    match value {
        b'A'..=b'Z' => Some((value + 32) as char),
        b'a'..=b'z' | b'0'..=b'9' | b'_' => Some(value as char),
        _ => None,
    }
}

fn word_counts(data: &[u8]) -> String {
    let mut counts = BTreeMap::<String, i64>::new();
    let mut word = String::new();
    for &value in data.iter().chain(std::iter::once(&0)) {
        if value >= 128 {
            return "ERR:TEXT".into();
        }
        if let Some(character) = normalized_character(value) {
            word.push(character);
        } else if !word.is_empty() {
            *counts.entry(std::mem::take(&mut word)).or_default() += 1;
        }
    }
    joined(counts.iter().map(|(word, count)| format!("{word}:{count}")))
}

enum Command {
    Add(u8, i64),
    Set(u8, i64),
    Erase(u8),
}

fn parse_command(record: &[u8]) -> Result<Command, &'static str> {
    let key = record[0];
    let amount = signed_word(record, 2);
    match record[1] {
        0 => Ok(Command::Add(key, amount)),
        1 => Ok(Command::Set(key, amount)),
        2 => Ok(Command::Erase(key)),
        _ => Err("ERR:OP"),
    }
}

fn ledger(data: &[u8]) -> String {
    if data.len() % 4 != 0 {
        return "ERR:LEN".into();
    }
    let mut balances = HashMap::<u8, i64>::new();
    for record in data.chunks_exact(4) {
        match parse_command(record) {
            Ok(Command::Add(key, amount)) => *balances.entry(key).or_default() += amount,
            Ok(Command::Set(key, amount)) => {
                balances.insert(key, amount);
            }
            Ok(Command::Erase(key)) => {
                balances.remove(&key);
            }
            Err(message) => return message.into(),
        }
    }
    let ordered: BTreeMap<_, _> = balances.into_iter().collect();
    let total: i64 = ordered.values().sum();
    format!(
        "{}|{}",
        total,
        joined(ordered.iter().map(|(key, value)| format!("{key}:{value}")))
    )
}

fn rank_records(data: &[u8]) -> String {
    if data.len() % 3 != 0 {
        return "ERR:LEN".into();
    }
    let mut records: Vec<_> = data
        .chunks_exact(3)
        .map(|record| (record[0], signed_word(record, 1)))
        .collect();
    records.sort_by_key(|&(_, score)| Reverse(score));
    joined(records.iter().map(|(id, score)| format!("{id}:{score}")))
}

fn schedule(data: &[u8]) -> String {
    if data.is_empty() || (data.len() - 1) % 2 != 0 {
        return "ERR:LEN".into();
    }
    let count = data[0] as usize;
    if count == 0 || count > 16 {
        return "ERR:NODES".into();
    }
    let mut edges = vec![BTreeSet::new(); count];
    let mut incoming = vec![0_usize; count];
    for pair in data[1..].chunks_exact(2) {
        let (from, to) = (pair[0] as usize, pair[1] as usize);
        if from >= count || to >= count {
            return "ERR:EDGE".into();
        }
        if edges[from].insert(to) {
            incoming[to] += 1;
        }
    }
    let mut ready: BinaryHeap<_> = (0..count)
        .filter(|&node| incoming[node] == 0)
        .map(Reverse)
        .collect();
    let mut order = Vec::new();
    while let Some(Reverse(node)) = ready.pop() {
        order.push(node);
        for &next in &edges[node] {
            incoming[next] -= 1;
            if incoming[next] == 0 {
                ready.push(Reverse(next));
            }
        }
    }
    if order.len() != count {
        "CYCLE".into()
    } else {
        joined(order.iter().map(|node| node.to_string()))
    }
}

fn abi_record(data: &[u8]) -> String {
    if data.len() != 17 {
        return "ERR:LEN".into();
    }
    let values: Vec<_> = (0..8)
        .map(|index| signed_word(data, 1 + index * 2))
        .collect();
    let [a, b, c, d, e, f, g, h] = values[..] else {
        unreachable!()
    };
    let result = match data[0] {
        0 => abi_bridge::wide_native([a, b, c, d, e, f, g, h]),
        1 => abi_bridge::wide_c(a, b, c, d, e, f, g, h),
        _ => return "ERR:MODE".into(),
    };
    format!("{}|{}|{}", result.weighted, result.spread, result.signature)
}

trait Policy {
    fn apply(&self, value: i64) -> i64;
}
struct Flat;
struct Tiered;
struct Rebate;
impl Policy for Flat {
    fn apply(&self, value: i64) -> i64 {
        value * 3 + 7
    }
}
impl Policy for Tiered {
    fn apply(&self, value: i64) -> i64 {
        if value > 100 {
            500 + (value - 100) * 2
        } else {
            value * 5
        }
    }
}
impl Policy for Rebate {
    fn apply(&self, value: i64) -> i64 {
        (-20).max(value - 13)
    }
}

fn apply_policy(data: &[u8]) -> String {
    if data.is_empty() || (data.len() - 1) % 2 != 0 {
        return "ERR:LEN".into();
    }
    let policy: Box<dyn Policy> = match data[0] {
        0 => Box::new(Flat),
        1 => Box::new(Tiered),
        2 => Box::new(Rebate),
        _ => return "ERR:MODE".into(),
    };
    let values: Vec<_> = (1..data.len())
        .step_by(2)
        .map(|offset| policy.apply(signed_word(data, offset)))
        .collect();
    let total: i64 = values.iter().sum();
    format!(
        "{}|{}",
        total,
        joined(values.iter().map(|value| value.to_string()))
    )
}

pub fn reconstructed(data: &[u8]) -> String {
    let handlers: [fn(&[u8]) -> String; 6] = [
        word_counts,
        ledger,
        rank_records,
        schedule,
        abi_record,
        apply_policy,
    ];
    match data.split_first() {
        Some((&point, body)) if (point as usize) < handlers.len() => handlers[point as usize](body),
        _ => "ERR:POINT".into(),
    }
}
