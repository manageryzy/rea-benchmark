package main

type policy func([]byte) uint64

//go:noinline
func addPolicy(input []byte) uint64 {
	value := uint64(3)
	for _, item := range input[1:] {
		value += uint64(item)
	}
	return value
}

//go:noinline
func xorPolicy(input []byte) uint64 {
	value := uint64(11)
	for index := 1; index < len(input); index++ {
		value ^= uint64(input[index]) << (8 * (index % 8))
	}
	return value
}

var policies = [...]policy{addPolicy, xorPolicy}

func familyRoot(input []byte) uint64 {
	if len(input) == 0 {
		return 0
	}
	choice := input[0] & 1
	return policies[choice](input) + uint64(choice)
}
