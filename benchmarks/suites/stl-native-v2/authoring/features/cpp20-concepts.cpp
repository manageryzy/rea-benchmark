#include <concepts>

template <typename Value>
    requires std::integral<Value>
constexpr Value add(Value left, Value right) {
    return left + right;
}

static_assert(add(4, 6) == 10);

int main() {
    return add(4, 6) == 10 ? 0 : 1;
}
