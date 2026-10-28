"""Readable, allocation-free Go controls for the shared buffer contract."""

VALUES = {
    'record_codec': '''value := uint64(0xffff)
    if len(data) >= 4 && data[0] == 0xa7 {
        count := uint32(data[1])
        decoded := uint32(data[2]) | uint32(data[3]) << 8
        value = uint64((count * decoded) ^ 0x6d35)
    }
    if len(data) > 4 {
        value += uint64(data[4])
    }
    return value''',
    'dispatch_registry': '''if len(data) == 0 {
        return 0
    }
    choice := data[0] & 1
    value := uint64(3)
    if choice != 0 {
        value = 11
    }
    for index := 1; index < len(data); index++ {
        if choice == 0 {
            value += uint64(data[index])
        } else {
            value ^= uint64(data[index]) << (8 * (index % 8))
        }
    }
    return value + uint64(choice)''',
    'numeric_aggregation': '''count := len(data) / 2
    if count > 8 {
        count = 8
    }
    var values [8]int16
    for index := 0; index < count; index++ {
        values[index] = int16(uint16(data[2*index]) | uint16(data[2*index+1]) << 8)
    }
    for index := 1; index < count; index++ {
        for cursor := index; cursor > 0 && values[cursor] < values[cursor-1]; cursor-- {
            values[cursor], values[cursor-1] = values[cursor-1], values[cursor]
        }
    }
    var total uint32
    for index := 0; index < count; index++ {
        total += uint32(int32(values[index])) * uint32(index+1)
    }
    return uint64(total ^ 0x9e3779b9)''',
    'variable_objects': '''if len(data) < 4 {
        return 0xff00 + 17
    }
    length := int(data[0]) | int(data[1]) << 8
    if length > len(data)-4 {
        return 0xff01 + 17
    }
    value := uint64(data[2])
    for _, item := range data[4:4+length] {
        value = value*33 + uint64(item)
    }
    return (value ^ uint64(data[3])) + 17''',
    'callback_policy': '''var total uint64
    for index, item := range data {
        total += (uint64(item)+1) * uint64(index+3)
    }
    return total + 9''',
    'session_state': '''var count, checksum uint32
    var result uint64
    for _, item := range data {
        count++
        checksum = checksum*17 + uint32(item)
        result ^= uint64(checksum ^ count)
    }
    return result''',
}


def solution(family):
    return '''package main

import "unsafe"

func evaluate(data []byte) uint64 {
    ''' + VALUES[family] + '''
}

func reconstructed(
    input unsafe.Pointer,
    inputLen uintptr,
    output unsafe.Pointer,
    outputCapacity uintptr,
    outputLen *uintptr,
) int32 {
    if inputLen > 512 {
        *outputLen = 0
        return -2
    }
    var snapshot [512]byte
    for index := uintptr(0); index < inputLen; index++ {
        snapshot[index] = *(*byte)(unsafe.Add(input, index))
    }
    value := evaluate(snapshot[:inputLen])
    required := inputLen + 8
    *outputLen = required
    if outputCapacity < required {
        return -1
    }
    var message [520]byte
    for index := uintptr(0); index < 8; index++ {
        message[index] = byte(value >> (8 * index))
    }
    for index := uintptr(0); index < inputLen; index++ {
        message[8+index] = snapshot[index] ^ message[index%8]
    }
    for index := uintptr(0); index < required; index++ {
        *(*byte)(unsafe.Add(output, index)) = message[index]
    }
    return 0
}
'''
