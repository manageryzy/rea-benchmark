// Composite, deterministic container workloads. C++11 is the baseline dialect;
// the final handler deliberately requires C++14 and is gated by __cplusplus.
#include <algorithm>
#include <array>
#include <bitset>
#include <cstdint>
#include <deque>
#include <forward_list>
#include <functional>
#include <iterator>
#include <list>
#include <map>
#include <memory>
#include <queue>
#include <set>
#include <sstream>
#include <stack>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <valarray>
#include <vector>

namespace stl_benchmark {
using Values = std::vector<int>;

template <class Range> std::string join(const Range &values) {
  std::ostringstream output;
  bool first = true;
  for (const auto &value : values) {
    if (!first)
      output << ',';
    first = false;
    output << value;
  }
  return first ? "-" : output.str();
}

template <class Range> std::string join_pairs(const Range &values) {
  std::ostringstream output;
  bool first = true;
  for (const auto &entry : values) {
    if (!first)
      output << ',';
    first = false;
    output << entry.first << ':' << entry.second;
  }
  return first ? "-" : output.str();
}

int bucket(int value) { return (value % 7 + 7) % 7; }

// Deliberate collisions exercise equality and chaining independently of hash
// dispersion. Output never depends on an unspecified unordered iteration order.
struct CollisionHash {
  std::size_t operator()(int) const noexcept { return 1; }
};

std::string vector_workload(const Values &input) {
  Values values;
  values.reserve(1);
  for (int value : input)
    values.emplace_back(value);
  if (!values.empty())
    std::rotate(values.begin(), values.begin() + 1, values.end());
  values.erase(std::remove_if(values.begin(), values.end(),
                              [](int x) { return x % 2 == 0; }),
               values.end());
  return join(values);
}

std::string array_workload(const Values &input) {
  if (input.size() != 8)
    return "ERR:LEN";
  std::array<int, 8> values{};
  std::copy(input.begin(), input.end(), values.begin());
  std::reverse(values.begin(), values.end());
  return join(values);
}

std::string deque_workload(const Values &input) {
  std::deque<int> values;
  for (std::size_t index = 0; index < input.size(); ++index) {
    if (index % 2 == 0)
      values.push_front(input[index]);
    else
      values.push_back(input[index]);
    if (values.size() > 5)
      values.pop_back();
  }
  return join(values);
}

std::string list_workload(const Values &input) {
  std::list<int> values(input.begin(), input.end());
  std::list<int> odd;
  for (auto position = values.begin(); position != values.end();) {
    auto current = position++;
    if (*current % 2 != 0)
      odd.splice(odd.end(), values, current);
  }
  values.splice(values.end(), odd);
  values.sort();
  values.unique();
  values.reverse();
  return join(values);
}

std::string forward_list_workload(const Values &input) {
  std::forward_list<int> values;
  auto tail = values.before_begin();
  for (int value : input)
    tail = values.insert_after(tail, value);
  values.reverse();
  values.remove_if([](int value) { return value < 0; });
  values.sort();
  values.unique();
  return join(values);
}

std::string set_workload(const Values &input) {
  std::set<int> values(input.begin(), input.end());
  if (!input.empty())
    values.erase(input.front());
  return join(values);
}

std::string multiset_workload(const Values &input) {
  std::multiset<int> values(input.begin(), input.end());
  if (!input.empty())
    values.erase(values.find(input.front())); // Erase one duplicate.
  return join(values);
}

std::string map_workload(const Values &input) {
  std::map<int, int> totals;
  for (int value : input)
    totals[bucket(value)] += value;
  return join_pairs(totals);
}

std::string multimap_workload(const Values &input) {
  std::multimap<int, int> values;
  for (int value : input)
    values.emplace(bucket(value), value);
  return join_pairs(values); // Equivalent keys retain insertion order.
}

std::string unordered_set_workload(const Values &input) {
  std::unordered_set<int, CollisionHash> values;
  values.max_load_factor(0.5f);
  values.reserve(1);
  values.insert(input.begin(), input.end());
  if (!input.empty())
    values.erase(input.front());
  Values ordered(values.begin(), values.end());
  std::sort(ordered.begin(), ordered.end());
  return join(ordered);
}

std::string unordered_multiset_workload(const Values &input) {
  std::unordered_multiset<int, CollisionHash> values(input.begin(),
                                                     input.end());
  values.rehash(1);
  if (!input.empty())
    values.erase(values.find(input.front()));
  Values ordered(values.begin(), values.end());
  std::sort(ordered.begin(), ordered.end());
  return join(ordered);
}

std::string unordered_map_workload(const Values &input) {
  std::unordered_map<int, int, CollisionHash> counts;
  counts.reserve(1);
  for (int value : input)
    ++counts[bucket(value)];
  std::map<int, int> ordered(counts.begin(), counts.end());
  return join_pairs(ordered);
}

std::string unordered_multimap_workload(const Values &input) {
  std::unordered_multimap<int, int, CollisionHash> values;
  values.reserve(1);
  for (int value : input)
    values.emplace(bucket(value), value);
  std::vector<std::pair<int, int>> ordered(values.begin(), values.end());
  std::sort(ordered.begin(), ordered.end());
  return join_pairs(ordered);
}

template <class Adaptor> std::string drain(Adaptor values) {
  Values output;
  while (!values.empty()) {
    output.push_back(values.top());
    values.pop();
  }
  return join(output);
}

std::string stack_workload(const Values &input) {
  std::stack<int> values;
  for (int value : input) {
    if (value >= 0)
      values.push(value);
    else if (!values.empty())
      values.pop();
  }
  return drain(std::move(values));
}

std::string queue_workload(const Values &input) {
  std::queue<int> values;
  for (int value : input) {
    if (value >= 0)
      values.emplace(value);
    else if (!values.empty())
      values.pop();
  }
  Values output;
  while (!values.empty()) {
    output.push_back(values.front());
    values.pop();
  }
  return join(output);
}

std::string priority_queue_workload(const Values &input) {
  std::priority_queue<int> values;
  for (int value : input) {
    if (value >= 0)
      values.emplace(value);
    else if (!values.empty())
      values.pop();
  }
  return drain(std::move(values));
}

std::string vector_bool_workload(const Values &input) {
  std::vector<bool> bits;
  for (int value : input)
    bits.push_back(value % 2 != 0);
  for (std::size_t index = 0; index < bits.size(); index += 3)
    bits[index].flip();
  std::string output;
  for (bool bit : bits)
    output.push_back(bit ? '1' : '0');
  return output.empty() ? "-" : output;
}

std::string string_workload(const Values &input) {
  std::string text;
  for (int value : input)
    text.push_back(static_cast<char>('a' + bucket(value)));
  text.insert(0, "[");
  text.append("]");
  return text.substr(1, text.size() - 2) + "|" + std::to_string(input.size());
}

std::string bitset_workload(const Values &input) {
  std::bitset<64> bits;
  for (int value : input)
    bits.flip(static_cast<std::size_t>((value % 64 + 64) % 64));
  return std::to_string(bits.count()) + "|" + bits.to_string();
}

std::string valarray_workload(const Values &input) {
  std::valarray<int> values(input.size());
  for (std::size_t index = 0; index < input.size(); ++index)
    values[index] = input[index];
  values = values * 2 + 3;
  Values output;
  for (std::size_t index = 0; index < values.size(); ++index)
    output.push_back(values[index]);
  return std::to_string(values.size() ? values.sum() : 0) + "|" + join(output);
}

enum class Policy { Triple = 3 };
constexpr int adjustment(int value) { return value + 7; }
static_assert(adjustment(0) == 7, "C++11 constexpr");

int sum_pack() { return 0; }
template <class First, class... Rest> int sum_pack(First first, Rest... rest) {
  return first + sum_pack(rest...);
}

struct Resource {
  int identity;
  Values &events;
  Resource(int identity, Values &events) : identity(identity), events(events) {
    events.push_back(identity);
  }
  Resource(const Resource &) = delete;
  Resource &operator=(const Resource &) = delete;
  ~Resource() noexcept { events.push_back(-identity); }
};

struct Transform {
  virtual ~Transform() {}
  virtual int apply(int value) const = 0;
};
struct Triple final : Transform {
  auto apply(int value) const -> int override {
    return sum_pack(value * static_cast<int>(Policy::Triple), adjustment(0));
  }
};

std::string grammar11_workload(const Values &input) {
  Values output;
  Values events;
  std::vector<std::unique_ptr<Resource>> resources;
  std::unique_ptr<Transform> transform(new Triple);
  const int offset = 2;
  auto record = [offset, &output, &transform](int value) {
    output.push_back(transform->apply(value) + offset);
  };
  bool failed = false;
  try {
    for (std::size_t index = 0; index < input.size(); ++index) {
      std::unique_ptr<Resource> resource(
          new Resource(static_cast<int>(index + 1), events));
      resources.emplace_back(std::move(resource));
      if (input[index] < 0)
        throw std::domain_error("negative");
      record(input[index]);
    }
  } catch (const std::domain_error &) {
    failed = true;
  }
  // Explicit reverse reset gives an observable, specified cleanup order.
  for (auto position = resources.rbegin(); position != resources.rend();
       ++position)
    position->reset();
  return std::string(failed ? "ERR|" : "OK|") + join(output) + "|" +
         join(events);
}

#if __cplusplus >= 201402L
#include "grammar14.inc"
#else
std::string grammar14_workload(const Values &) { return "ERR:STANDARD"; }
#endif

using Handler = std::string (*)(const Values &);
const Handler handlers[] = {vector_workload,
                            array_workload,
                            deque_workload,
                            list_workload,
                            forward_list_workload,
                            set_workload,
                            multiset_workload,
                            map_workload,
                            multimap_workload,
                            unordered_set_workload,
                            unordered_multiset_workload,
                            unordered_map_workload,
                            unordered_multimap_workload,
                            stack_workload,
                            queue_workload,
                            priority_queue_workload,
                            vector_bool_workload,
                            string_workload,
                            bitset_workload,
                            valarray_workload,
                            grammar11_workload,
                            grammar14_workload};
} // namespace stl_benchmark

std::string reconstructed(const std::vector<std::uint8_t> &data) {
  using namespace stl_benchmark;
  if (data.empty() || data[0] >= sizeof(handlers) / sizeof(handlers[0]))
    return "ERR:POINT";
  if (data.size() % 2 == 0)
    return "ERR:LEN";
  Values values;
  for (std::size_t index = 1; index < data.size(); index += 2) {
    const int raw = data[index] | (static_cast<int>(data[index + 1]) << 8);
    values.push_back(raw >= 32768 ? raw - 65536 : raw);
  }
  return handlers[data[0]](values);
}
