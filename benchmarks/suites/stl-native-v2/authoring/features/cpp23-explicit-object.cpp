struct Counter {
    int value;

    constexpr int read(this const Counter& self) {
        return self.value;
    }
};

static_assert(Counter{7}.read() == 7);

int main() {
    return Counter{7}.read() == 7 ? 0 : 1;
}
