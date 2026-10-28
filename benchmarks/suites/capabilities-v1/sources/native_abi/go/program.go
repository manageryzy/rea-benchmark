package main

import (
	"bufio"
	"fmt"
	"math/bits"
	"os"
	"strconv"
	"strings"
)

type Packet struct {
	First, Second, Third uint64
}

type Reply struct {
	First, Second, Third uint64
}

//go:noinline
func nativeTransform(packet Packet, nonce uint64) Reply {
	first := bits.RotateLeft64(packet.First^nonce, 11) + packet.Second*17
	second := bits.RotateLeft64(packet.Second+0x7a6d503912c4b8ef, 23) ^ packet.Third
	third := packet.Third + nonce*257 ^ bits.RotateLeft64(first+second, 5)
	return Reply{first, second, third}
}

// A function value retains a real ABIInternal call. A probe can replace this
// descriptor without modifying either original caller or original callee code.
var replacementSlot = nativeTransform

//go:noinline
func nativeCaller(packet Packet, nonce uint64) Reply {
	reply := replacementSlot(packet, nonce)
	reply.Third ^= 0x4c839e1720b6d5fa
	return reply
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	output := bufio.NewWriter(os.Stdout)
	defer output.Flush()
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) != 5 {
			panic("expected selector, nonce and three packet fields")
		}
		selected, err := strconv.ParseUint(fields[0], 10, 32)
		if err != nil {
			panic(err)
		}
		var values [4]uint64
		for index := range values {
			values[index], err = strconv.ParseUint(fields[index+1], 16, 64)
			if err != nil {
				panic(err)
			}
		}
		packet := Packet{values[1], values[2], values[3]}
		var reply Reply
		switch selected {
		case 0:
			reply = nativeTransform(packet, values[0])
		case 1:
			reply = nativeCaller(packet, values[0])
		default:
			panic("unknown selector")
		}
		fmt.Fprintf(output, "%d %016x %016x %016x\n", selected,
			reply.First, reply.Second, reply.Third)
	}
	if err := scanner.Err(); err != nil {
		panic(err)
	}
}
