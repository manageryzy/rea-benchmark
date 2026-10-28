"""Reviewed semantic/code-generation controls. Never distributed to RE tasks."""

LOGIC = {
    'record_codec': r'''
static uint64_t logic(const uint8_t *data, size_t size) {
    uint64_t value = 65535;
    if (size >= 4 && data[0] == 0xa7) {
        uint32_t item = (uint32_t)data[2] | (uint32_t)data[3] << 8;
        value = ((uint32_t)data[1] * item) ^ 0x6d35U;
    }
    return value + (size > 4 ? data[4] : 0);
}
''',
    'dispatch_registry': r'''
static uint64_t logic(const uint8_t *data, size_t size) {
    if (size == 0) return 0;
    unsigned choice = data[0] & 1;
    uint64_t value = choice ? 11 : 3;
    for (size_t index = 1; index < size; ++index) {
        if (choice) value ^= (uint64_t)data[index] << ((index % 8) * 8);
        else value += data[index];
    }
    return value + choice;
}
''',
    'numeric_aggregation': r'''
static uint64_t logic(const uint8_t *data, size_t size) {
    int32_t values[8];
    size_t count = size / 2 < 8 ? size / 2 : 8;
    for (size_t index = 0; index < count; ++index) {
        uint32_t bits = (uint32_t)data[2 * index] | (uint32_t)data[2 * index + 1] << 8;
        values[index] = bits >= 32768 ? (int32_t)bits - 65536 : (int32_t)bits;
        size_t position = index;
        while (position && values[position] < values[position - 1]) {
            int32_t previous = values[position - 1];
            values[position - 1] = values[position];
            values[position] = previous;
            --position;
        }
    }
    uint32_t total = 0;
    for (size_t index = 0; index < count; ++index)
        total += (uint32_t)values[index] * (uint32_t)(index + 1);
    return total ^ 0x9e3779b9U;
}
''',
    'variable_objects': r'''
static uint64_t logic(const uint8_t *data, size_t size) {
    if (size < 4) return 0xff00U + 17;
    uint32_t length = (uint32_t)data[0] | (uint32_t)data[1] << 8;
    if (length > size - 4) return 0xff01U + 17;
    uint64_t value = data[2];
    for (size_t index = 0; index < length; ++index)
        value = value * 33 + data[4 + index];
    return (value ^ data[3]) + 17;
}
''',
    'callback_policy': r'''
static uint64_t logic(const uint8_t *data, size_t size) {
    uint64_t value = 0;
    for (size_t index = 0; index < size; ++index)
        value += ((uint64_t)data[index] + 1) * (index + 3);
    return value + 9;
}
''',
    'session_state': r'''
static uint64_t logic(const uint8_t *data, size_t size) {
    uint32_t count = 0, checksum = 0;
    uint64_t value = 0;
    for (size_t index = 0; index < size; ++index) {
        count += 1;
        checksum = checksum * 17U + data[index];
        value ^= checksum ^ count;
    }
    return value;
}
'''
}

BUFFER = r'''
int32_t reconstructed(const uint8_t *input, size_t input_len,
                      uint8_t *output, size_t capacity, size_t *output_len) {
    if (input_len > 512) { *output_len = 0; return -2; }
    uint64_t result = logic(input, input_len);
    size_t required = 8 + input_len;
    *output_len = required;
    if (capacity < required) return -1;
    uint8_t message[520];
    for (size_t index = 0; index < 8; ++index)
        message[index] = (uint8_t)(result >> (8 * index));
    for (size_t index = 0; index < input_len; ++index)
        message[8 + index] = input[index] ^ message[index % 8];
    for (size_t index = 0; index < required; ++index)
        output[index] = message[index];
    return 0;
}
'''


def solution(family):
    return LOGIC[family] + BUFFER
