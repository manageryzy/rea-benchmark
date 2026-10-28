#include <string.h>

typedef struct {
    uint32_t checksum;
    uint32_t count;
    uint32_t multiplier;
    uint32_t alive;
} Context;

int32_t reconstructed(uint8_t *storage, uint32_t operation, uint32_t value,
                      uint32_t limit, uint64_t *output, session_callback callback) {
    (void)callback;
    Context context;
    memcpy(&context, storage, sizeof(context));
    if (operation > 4) return -1;
    if (operation == 0 && context.alive) return -5;
    if (operation != 0 && !context.alive) return -2;
    if ((operation == 0 || operation == 2) && (limit == 0 || limit > 31)) return -1;
    if (operation == 0 || operation == 2) {
        context.alive = 1;
        context.checksum = value;
        context.count = 0;
        context.multiplier = limit;
    } else if (operation == 3) {
        context.alive = 0;
    } else {
        if (operation == 1) {
            context.count += 1;
            context.checksum = context.checksum * context.multiplier + value + context.count;
        }
        *output = ((uint64_t)context.checksum << 32) | context.count;
    }
    memcpy(storage, &context, sizeof(context));
    return 0;
}
