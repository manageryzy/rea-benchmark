constexpr int adjust(int value) {
    if consteval {
        return value + 1;
    } else {
        return value + 2;
    }
}

static_assert(adjust(3) == 4);

int main(int argument_count, char**) {
    // The runtime path must remain distinct from constant evaluation.
    return adjust(argument_count) == argument_count + 2 ? 0 : 1;
}
