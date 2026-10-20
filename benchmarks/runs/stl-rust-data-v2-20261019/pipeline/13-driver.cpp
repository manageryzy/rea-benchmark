#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

std::string reconstructed(const std::vector<std::uint8_t> &data);

int main() {
  std::string line;
  while (std::getline(std::cin, line)) {
    std::vector<std::uint8_t> bytes;
    for (std::size_t index = 0; index + 1 < line.size(); index += 2)
      bytes.push_back(static_cast<std::uint8_t>(
          std::stoul(line.substr(index, 2), nullptr, 16)));
    std::cout << reconstructed(bytes) << '\n';
  }
}
