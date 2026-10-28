package main

import "encoding/binary"

func decodeRecord(input []byte) uint64 {
	if len(input) < 4 || input[0] != 0xa7 {
		return 0xffff
	}
	count := uint32(input[1])
	value := uint32(binary.LittleEndian.Uint16(input[2:4]))
	return uint64(count*value ^ 0x6d35)
}

func familyRoot(input []byte) uint64 {
	value := decodeRecord(input)
	if len(input) > 4 {
		value += uint64(input[4])
	}
	return value
}
