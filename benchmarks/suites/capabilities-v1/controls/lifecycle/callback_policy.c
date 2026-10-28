#include <string.h>

/* Deliberately uses a different private layout and inactive representation. */
typedef struct {
    uint32_t alive;
    uint32_t checksum;
    uint32_t count;
    uint32_t remaining;
} Context;

int32_t reconstructed(uint8_t *storage, uint32_t operation, uint32_t value,
                      uint32_t limit, uint64_t *output, session_callback callback) {
    Context context;
    memcpy(&context, storage, sizeof(context));
    if (operation > 4) return -1;
    if (operation == 0 && context.alive) return -5;
    if (operation != 0 && !context.alive) return -2;
    if ((operation == 0 || operation == 2) && (limit < 1 || limit > 8)) return -1;
    if (operation == 1 && value > 255) return -1;

    if (operation == 4) {
        *output = ((uint64_t)context.checksum << 32) + context.count;
        return 0;
    }
    if (operation == 1 && !context.remaining) return -4;
    uint64_t first = operation == 3 ? context.checksum : value;
    uint64_t second = (operation == 0 || operation == 2) ? limit : context.count;
    uint64_t response = callback(operation, first, second);
    if (response == UINT64_MAX) return -3;

    switch (operation) {
    case 0:
    case 2:
        context.alive = 1;
        context.checksum = value ^ (uint32_t)response;
        context.count = 0;
        context.remaining = limit;
        break;
    case 1:
        context.checksum = (context.checksum * 33) ^ value ^ (uint32_t)response;
        context.count += 1;
        context.remaining -= 1;
        *output = ((uint64_t)context.checksum << 32) + context.count;
        break;
    case 3:
        context.alive = 0;
        break;
    }
    memcpy(storage, &context, sizeof(context));
    return 0;
}
