package main

import "encoding/binary"

type header struct {
	length uint16
	tag    byte
	flags  byte
}

func inspectPayload(input []byte) uint64 {
	if len(input) < 4 {
		return 0xff00
	}
	record := header{binary.LittleEndian.Uint16(input[:2]), input[2], input[3]}
	if int(record.length) > len(input)-4 {
		return 0xff01
	}
	value := uint64(record.tag)
	for _, item := range input[4 : 4+int(record.length)] {
		value = value*33 + uint64(item)
	}
	return value ^ uint64(record.flags)
}

func familyRoot(input []byte) uint64 {
	return inspectPayload(input) + 17
}
