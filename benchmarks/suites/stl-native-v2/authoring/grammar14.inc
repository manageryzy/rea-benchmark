// Included only in the C++14 build.
constexpr int triangular(int count) {
  int total = 0;
  for (int index = 1; index <= count; ++index)
    total += index;
  return total;
}
static_assert(triangular(3) == 6, "C++14 constexpr loop");

template <class Container> decltype(auto) first_reference(Container &values) {
  return (values.front());
}
auto make_values() { return Values{}; }

std::string grammar14_workload(const Values &input) {
  auto output = make_values();
  auto transform = [offset = std::make_unique<int>(triangular(0b11))](
                       auto value) { return value * 3 + *offset; };
  for (auto value : input)
    output.emplace_back(transform(value));
  if (!output.empty())
    first_reference(output) += 1;
  std::stable_sort(
      output.begin(), output.end(),
      [](const auto &left, const auto &right) { return left > right; });
  std::map<int, int, std::less<>> counts;
  for (auto value : input)
    ++counts[bucket(value)];
  const auto found =
      counts.find(1L); // Transparent comparator / heterogeneous key.
  return join(output) + "|" +
         std::to_string(found == counts.end() ? 0 : found->second) + "|" +
         std::to_string(1'024);
}
