#include <type_traits>

template <typename... Values>
using First = Values...[0];

static_assert(std::is_same_v<First<int, char, double>, int>);

int main() {
    return std::is_same_v<First<int, char, double>, int> ? 0 : 1;
}
