package main

type session struct {
	count    uint32
	checksum uint32
}

func (state *session) update(value byte) uint64 {
	state.count++
	state.checksum = state.checksum*17 + uint32(value)
	return uint64(state.checksum ^ state.count)
}

func familyRoot(input []byte) uint64 {
	state := session{}
	var value uint64
	for _, item := range input {
		value ^= state.update(item)
	}
	return value
}
