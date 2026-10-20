// Optional later-standard fixtures. The builder probes the actual standard
// library; missing facilities are reported as unavailable, never emulated.
#include <algorithm>
#include <cstdint>
#include <sstream>
#include <string>
#include <vector>

#if CONTAINER_KIND == 0 || CONTAINER_KIND == 1
#include <flat_map>
#elif CONTAINER_KIND == 2 || CONTAINER_KIND == 3
#include <flat_set>
#elif CONTAINER_KIND == 4
#include <inplace_vector>
#elif CONTAINER_KIND == 5
#include <hive>
#endif

std::string reconstructed(const std::vector<std::uint8_t> &data) {
  if (data.empty() || data[0] != 0)
    return "ERR:POINT";
  if (data.size() % 2 == 0)
    return "ERR:LEN";
  std::vector<int> input;
  for (std::size_t index = 1; index < data.size(); index += 2) {
    int raw = data[index] | (static_cast<int>(data[index + 1]) << 8);
    input.push_back(raw >= 32768 ? raw - 65536 : raw);
  }
  auto bucket = [](int value) { return (value % 7 + 7) % 7; };
  (void)bucket;
  std::ostringstream output;
  bool first = true;
  auto emit = [&](const auto &value) {
    if (!first)
      output << ',';
    first = false;
    output << value;
  };
#if CONTAINER_KIND == 0
  std::flat_map<int, int> values;
  for (int value : input)
    values[bucket(value)] += value;
  for (const auto &entry : values)
    emit(std::to_string(entry.first) + ":" + std::to_string(entry.second));
#elif CONTAINER_KIND == 1
  std::flat_multimap<int, int> values;
  for (int value : input)
    values.emplace(bucket(value), value);
  for (const auto &entry : values)
    emit(std::to_string(entry.first) + ":" + std::to_string(entry.second));
#elif CONTAINER_KIND == 2
  std::flat_set<int> values(input.begin(), input.end());
  if (!input.empty())
    values.erase(input.front());
  for (int value : values)
    emit(value);
#elif CONTAINER_KIND == 3
  std::flat_multiset<int> values(input.begin(), input.end());
  if (!input.empty())
    values.erase(values.find(input.front()));
  for (int value : values)
    emit(value);
#elif CONTAINER_KIND == 4
  std::inplace_vector<int, 64> values;
  if (input.size() > values.capacity())
    return "ERR:CAP";
  for (int value : input)
    values.emplace_back(value);
  std::reverse(values.begin(), values.end());
  for (int value : values)
    emit(value);
#elif CONTAINER_KIND == 5
  std::hive<int> values(input.begin(), input.end());
  for (auto position = values.begin(); position != values.end();) {
    if (*position < 0)
      position = values.erase(position);
    else
      ++position;
  }
  for (int value : input)
    if (value < 0)
      values.insert(-value);
  values.sort();
  for (int value : values)
    emit(value);
#else
#error Unknown CONTAINER_KIND
#endif
  return first ? "-" : output.str();
}
