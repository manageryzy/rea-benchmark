package main

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"unsafe"
)

//go:noinline
func familyBuffer(input *byte, inputLen uintptr, output *byte, capacity uintptr, outputLen *uintptr) int32 {
	if inputLen > 512 {
		*outputLen = 0
		return -2
	}
	// Snapshot before writing so overlap has the same meaning in every language.
	var snapshot [512]byte
	copy(snapshot[:], unsafe.Slice(input, inputLen))
	value := familyRoot(snapshot[:inputLen])
	required := inputLen + 8
	*outputLen = required
	if capacity < required {
		return -1
	}
	var message [520]byte
	for index := 0; index < 8; index++ {
		message[index] = byte(value >> (8 * index))
	}
	for index := uintptr(0); index < inputLen; index++ {
		message[8+index] = snapshot[index] ^ message[index%8]
	}
	copy(unsafe.Slice(output, required), message[:required])
	return 0
}

func main() {
	input := bufio.NewReader(os.Stdin)
	output := bufio.NewWriter(os.Stdout)
	defer output.Flush()
	for {
		var id uint64
		var size, capacity, offset, seed uint
		var encoded string
		_, err := fmt.Fscan(input, &id, &size, &capacity, &offset, &seed, &encoded)
		if err == io.EOF {
			return
		}
		if err != nil || size > 513 || capacity > 520 || offset > 2048 || capacity > 2048-offset || seed > 255 {
			os.Exit(2)
		}
		var data []byte
		if size == 0 {
			if encoded != "-" {
				os.Exit(3)
			}
		} else {
			data, err = hex.DecodeString(encoded)
			if err != nil || len(data) != int(size) {
				os.Exit(3)
			}
		}
		var arena [2048]byte
		for index := range arena {
			arena[index] = byte(seed + uint(37*index))
		}
		copy(arena[64:], data)
		outputLen := ^uintptr(0)
		outputPointer := (*byte)(unsafe.Add(unsafe.Pointer(&arena[0]), uintptr(offset)))
		status := familyBuffer(&arena[64], uintptr(size), outputPointer, uintptr(capacity), &outputLen)
		fmt.Fprintf(output, "%d %d %d %s\n", id, status, outputLen, hex.EncodeToString(arena[:]))
	}
}
