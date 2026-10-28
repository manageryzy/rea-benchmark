#include <cstddef>

// Guest-only byte helpers. No host services or grading data are linked.
extern "C" {
__attribute__((weak)) void *memcpy(void *dst, const void *src, std::size_t size) {
    auto *out = static_cast<unsigned char *>(dst);
    const auto *in = static_cast<const unsigned char *>(src);
    for (std::size_t i = 0; i < size; ++i) out[i] = in[i];
    return dst;
}
__attribute__((weak)) void *memmove(void *dst, const void *src, std::size_t size) {
    auto *out = static_cast<unsigned char *>(dst);
    const auto *in = static_cast<const unsigned char *>(src);
    if (reinterpret_cast<std::size_t>(out) < reinterpret_cast<std::size_t>(in)) {
        for (std::size_t i = 0; i < size; ++i) out[i] = in[i];
    } else {
        while (size) { --size; out[size] = in[size]; }
    }
    return dst;
}
__attribute__((weak)) void *memset(void *dst, int byte, std::size_t size) {
    auto *out = static_cast<unsigned char *>(dst);
    for (std::size_t i = 0; i < size; ++i) out[i] = static_cast<unsigned char>(byte);
    return dst;
}
__attribute__((weak)) int memcmp(const void *left, const void *right, std::size_t size) {
    const auto *a = static_cast<const unsigned char *>(left);
    const auto *b = static_cast<const unsigned char *>(right);
    for (std::size_t i = 0; i < size; ++i)
        if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
}
__attribute__((weak)) std::size_t strlen(const char *text) {
    std::size_t size = 0;
    while (text[size]) ++size;
    return size;
}
}
