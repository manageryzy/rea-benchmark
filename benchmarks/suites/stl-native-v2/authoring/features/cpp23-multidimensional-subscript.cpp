struct Grid {
    constexpr int operator[](int row, int column) const {
        return row * 10 + column;
    }
};

static_assert(Grid{}[2, 3] == 23);

int main() {
    return Grid{}[2, 3] == 23 ? 0 : 1;
}
