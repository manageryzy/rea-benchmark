#include "abi.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <set>
#include <span>
#include <sstream>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

using Bytes = std::span<const std::uint8_t>;

std::int64_t signed_word(Bytes data, std::size_t offset) {
    const auto value = std::uint32_t(data[offset]) | (std::uint32_t(data[offset + 1]) << 8);
    return value >= 32768 ? std::int64_t(value) - 65536 : value;
}

template <typename Range, typename Render>
std::string join_items(const Range &items, Render render) {
    std::string result;
    for (const auto &item : items) {
        if (!result.empty()) {
            result += ',';
        }
        result += render(item);
    }
    return result.empty() ? "-" : result;
}

std::optional<char> normalized_character(std::uint8_t value) {
    if (value >= 'A' && value <= 'Z') {
        return char(value + ('a' - 'A'));
    }
    if ((value >= 'a' && value <= 'z') || (value >= '0' && value <= '9') || value == '_') {
        return char(value);
    }
    return std::nullopt;
}

std::string word_counts(Bytes data) {
    std::map<std::string, std::int64_t> counts;
    std::string word;
    const auto flush = [&] {
        if (!word.empty()) {
            ++counts[word];
            word.clear();
        }
    };
    for (auto value : data) {
        if (value >= 128) {
            return "ERR:TEXT";
        }
        if (const auto normalized = normalized_character(value)) {
            word += *normalized;
        } else {
            flush();
        }
    }
    flush();
    return join_items(
        counts, [](const auto &item) { return item.first + ':' + std::to_string(item.second); });
}

struct Add {
    std::int64_t amount;
};
struct Set {
    std::int64_t amount;
};
struct Erase {};
using Command = std::variant<Add, Set, Erase>;

std::string ledger(Bytes data) {
    if (data.size() % 4 != 0) {
        return "ERR:LEN";
    }
    std::unordered_map<unsigned, std::int64_t> balances;
    for (std::size_t offset = 0; offset < data.size(); offset += 4) {
        const auto key = data[offset];
        const auto operation = data[offset + 1];
        if (operation > 2) {
            return "ERR:OP";
        }
        const auto amount = signed_word(data, offset + 2);
        const Command command = operation == 0   ? Command(Add{amount})
                                : operation == 1 ? Command(Set{amount})
                                                 : Command(Erase{});
        std::visit(
            [&](const auto &action) {
                using Action = std::decay_t<decltype(action)>;
                if constexpr (std::is_same_v<Action, Add>) {
                    balances[key] += action.amount;
                } else if constexpr (std::is_same_v<Action, Set>) {
                    balances[key] = action.amount;
                } else {
                    balances.erase(key);
                }
            },
            command);
    }
    const std::map<unsigned, std::int64_t> ordered(balances.begin(), balances.end());
    std::int64_t total = 0;
    for (const auto &[key, balance] : ordered) {
        total += balance;
    }
    return std::to_string(total) + '|' + join_items(ordered, [](const auto &item) {
               return std::to_string(item.first) + ':' + std::to_string(item.second);
           });
}

struct RankedRecord {
    unsigned id;
    std::int64_t score;
};

std::string rank_records(Bytes data) {
    if (data.size() % 3 != 0) {
        return "ERR:LEN";
    }
    std::vector<RankedRecord> records;
    for (std::size_t offset = 0; offset < data.size(); offset += 3) {
        records.push_back({data[offset], signed_word(data, offset + 1)});
    }
    std::stable_sort(records.begin(), records.end(),
                     [](const auto &a, const auto &b) { return a.score > b.score; });
    return join_items(records, [](const auto &record) {
        return std::to_string(record.id) + ':' + std::to_string(record.score);
    });
}

std::string schedule(Bytes data) {
    if (data.empty() || (data.size() - 1) % 2 != 0) {
        return "ERR:LEN";
    }
    const unsigned count = data[0];
    if (count == 0 || count > 16) {
        return "ERR:NODES";
    }
    std::vector<std::set<unsigned>> edges(count);
    std::vector<unsigned> incoming(count);
    for (std::size_t offset = 1; offset < data.size(); offset += 2) {
        const unsigned from = data[offset], to = data[offset + 1];
        if (from >= count || to >= count) {
            return "ERR:EDGE";
        }
        if (edges[from].insert(to).second) {
            ++incoming[to];
        }
    }
    std::priority_queue<unsigned, std::vector<unsigned>, std::greater<>> ready;
    for (unsigned node = 0; node < count; ++node) {
        if (incoming[node] == 0) {
            ready.push(node);
        }
    }
    std::vector<unsigned> order;
    while (!ready.empty()) {
        const auto node = ready.top();
        ready.pop();
        order.push_back(node);
        for (auto next : edges[node]) {
            if (--incoming[next] == 0) {
                ready.push(next);
            }
        }
    }
    return order.size() == count ? join_items(order, [](auto node) { return std::to_string(node); })
                                 : "CYCLE";
}

std::string abi_record(Bytes data) {
    if (data.size() != 17) {
        return "ERR:LEN";
    }
    if (data[0] > 1) {
        return "ERR:MODE";
    }
    std::array<std::int64_t, 8> values{};
    for (std::size_t index = 0; index < values.size(); ++index) {
        values[index] = signed_word(data, 1 + index * 2);
    }
    const auto [a, b, c, d, e, f, g, h] = values;
    const Summary result =
        data[0] == 0 ? wide_sysv(a, b, c, d, e, f, g, h) : wide_ms(a, b, c, d, e, f, g, h);
    return std::to_string(result.weighted) + '|' + std::to_string(result.spread) + '|' +
           std::to_string(result.signature);
}

class Policy {
  public:
    virtual ~Policy() = default;
    virtual std::int64_t apply(std::int64_t value) const = 0;
};

class Flat final : public Policy {
  public:
    std::int64_t apply(std::int64_t value) const override { return value * 3 + 7; }
};

class Tiered final : public Policy {
  public:
    std::int64_t apply(std::int64_t value) const override {
        return value > 100 ? 500 + (value - 100) * 2 : value * 5;
    }
};

class Rebate final : public Policy {
  public:
    std::int64_t apply(std::int64_t value) const override {
        return std::max<std::int64_t>(-20, value - 13);
    }
};

std::string apply_policy(Bytes data) {
    if (data.empty() || (data.size() - 1) % 2 != 0) {
        return "ERR:LEN";
    }
    std::unique_ptr<Policy> policy;
    switch (data[0]) {
    case 0:
        policy = std::make_unique<Flat>();
        break;
    case 1:
        policy = std::make_unique<Tiered>();
        break;
    case 2:
        policy = std::make_unique<Rebate>();
        break;
    default:
        return "ERR:MODE";
    }
    std::vector<std::int64_t> results;
    std::int64_t total = 0;
    for (std::size_t offset = 1; offset < data.size(); offset += 2) {
        const auto value = policy->apply(signed_word(data, offset));
        results.push_back(value);
        total += value;
    }
    return std::to_string(total) + '|' +
           join_items(results, [](auto value) { return std::to_string(value); });
}

std::string reconstructed(Bytes data) {
    using Handler = std::string (*)(Bytes);
    static const std::array<Handler, 6> handlers{word_counts, ledger,     rank_records,
                                                 schedule,    abi_record, apply_policy};
    if (data.empty() || data[0] >= handlers.size()) {
        return "ERR:POINT";
    }
    return handlers[data[0]](data.subspan(1));
}
