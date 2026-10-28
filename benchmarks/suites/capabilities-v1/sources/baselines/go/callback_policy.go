package main

type callback func(byte, int) uint64

//go:noinline
func transformItem(value byte, index int) uint64 {
	return (uint64(value) + 1) * uint64(index+3)
}

//go:noinline
func applyCallback(input []byte, selected callback) uint64 {
	var result uint64
	for index, value := range input {
		result += selected(value, index)
	}
	return result
}

var itemTransform callback = transformItem

func familyRoot(input []byte) uint64 {
	return applyCallback(input, itemTransform) + 9
}
