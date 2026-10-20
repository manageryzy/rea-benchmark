struct Double {
    static constexpr int operator()(int value) {
        return value * 2;
    }
};

static_assert(Double{}(4) == 8);

int main() {
    return Double{}(4) == 8 ? 0 : 1;
}
