package main

import (
	"encoding/binary"
	"sort"
)

func familyRoot(input []byte) uint64 {
	count := len(input) / 2
	if count > 8 {
		count = 8
	}
	values := make([]int16, count)
	for index := range values {
		values[index] = int16(binary.LittleEndian.Uint16(input[2*index : 2*index+2]))
	}
	sort.Slice(values, func(left, right int) bool {
		return values[left] < values[right]
	})
	var total uint32
	for index, value := range values {
		total += uint32(value) * uint32(index+1)
	}
	return uint64(total ^ 0x9e3779b9)
}
