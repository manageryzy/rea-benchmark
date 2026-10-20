package main

import (
	"container/heap"
	"encoding/binary"
	"fmt"
	"sort"
	"strconv"
	"strings"
)

func signedWord(data []byte, offset int) int64 {
	return int64(int16(binary.LittleEndian.Uint16(data[offset:])))
}

func joinNumbers[T ~int | ~int64](values []T) string {
	parts := make([]string, len(values))
	for index, value := range values {
		parts[index] = fmt.Sprint(value)
	}
	return joinItems(parts)
}

func joinItems(parts []string) string {
	if len(parts) == 0 {
		return "-"
	}
	return strings.Join(parts, ",")
}

func wordCounts(data []byte) string {
	counts := make(map[string]int)
	var word strings.Builder
	flush := func() {
		if word.Len() > 0 {
			counts[word.String()]++
			word.Reset()
		}
	}
	for _, value := range data {
		if value >= 128 {
			return "ERR:TEXT"
		}
		if value >= 'A' && value <= 'Z' {
			value += 32
		}
		if value >= 'a' && value <= 'z' || value >= '0' && value <= '9' || value == '_' {
			word.WriteByte(value)
		} else {
			flush()
		}
	}
	flush()
	keys := make([]string, 0, len(counts))
	for key := range counts {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, key+":"+strconv.Itoa(counts[key]))
	}
	return joinItems(parts)
}

type command struct {
	key       byte
	operation byte
	amount    int64
}

func parseCommand(record []byte) (command, error) {
	if record[1] > 2 {
		return command{}, fmt.Errorf("ERR:OP")
	}
	return command{record[0], record[1], signedWord(record, 2)}, nil
}

func ledger(data []byte) string {
	if len(data)%4 != 0 {
		return "ERR:LEN"
	}
	balances := make(map[byte]int64)
	for offset := 0; offset < len(data); offset += 4 {
		action, err := parseCommand(data[offset : offset+4])
		if err != nil {
			return err.Error()
		}
		switch action.operation {
		case 0:
			balances[action.key] += action.amount
		case 1:
			balances[action.key] = action.amount
		case 2:
			delete(balances, action.key)
		}
	}
	keys := make([]int, 0, len(balances))
	var total int64
	for key, value := range balances {
		keys = append(keys, int(key))
		total += value
	}
	sort.Ints(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, fmt.Sprintf("%d:%d", key, balances[byte(key)]))
	}
	return fmt.Sprintf("%d|%s", total, joinItems(parts))
}

type rankedRecord struct {
	id    byte
	score int64
}

func rankRecords(data []byte) string {
	if len(data)%3 != 0 {
		return "ERR:LEN"
	}
	records := make([]rankedRecord, 0, len(data)/3)
	for offset := 0; offset < len(data); offset += 3 {
		records = append(records, rankedRecord{data[offset], signedWord(data, offset+1)})
	}
	sort.SliceStable(records, func(i, j int) bool { return records[i].score > records[j].score })
	parts := make([]string, 0, len(records))
	for _, record := range records {
		parts = append(parts, fmt.Sprintf("%d:%d", record.id, record.score))
	}
	return joinItems(parts)
}

type nodeHeap []int

func (values nodeHeap) Len() int           { return len(values) }
func (values nodeHeap) Less(i, j int) bool { return values[i] < values[j] }
func (values nodeHeap) Swap(i, j int)      { values[i], values[j] = values[j], values[i] }
func (values *nodeHeap) Push(value any)    { *values = append(*values, value.(int)) }
func (values *nodeHeap) Pop() any {
	last := len(*values) - 1
	value := (*values)[last]
	*values = (*values)[:last]
	return value
}

func schedule(data []byte) string {
	if len(data) == 0 || (len(data)-1)%2 != 0 {
		return "ERR:LEN"
	}
	count := int(data[0])
	if count == 0 || count > 16 {
		return "ERR:NODES"
	}
	edges := make([]map[int]struct{}, count)
	incoming := make([]int, count)
	for node := range edges {
		edges[node] = make(map[int]struct{})
	}
	for offset := 1; offset < len(data); offset += 2 {
		from, to := int(data[offset]), int(data[offset+1])
		if from >= count || to >= count {
			return "ERR:EDGE"
		}
		if _, exists := edges[from][to]; !exists {
			edges[from][to] = struct{}{}
			incoming[to]++
		}
	}
	ready := &nodeHeap{}
	for node, degree := range incoming {
		if degree == 0 {
			heap.Push(ready, node)
		}
	}
	order := make([]int, 0, count)
	for ready.Len() > 0 {
		node := heap.Pop(ready).(int)
		order = append(order, node)
		for next := range edges[node] {
			incoming[next]--
			if incoming[next] == 0 {
				heap.Push(ready, next)
			}
		}
	}
	if len(order) != count {
		return "CYCLE"
	}
	return joinNumbers(order)
}

type summary struct{ weighted, spread, signature int64 }

func wideValue(a, b, c, d, e, f, g, h int64) summary {
	values := []int64{a, b, c, d, e, f, g, h}
	low, high := a, a
	for _, value := range values {
		if value < low {
			low = value
		}
		if value > high {
			high = value
		}
	}
	return summary{a + 2*b - 3*c + 5*d + 7*e - 11*f + 13*g - 17*h, high - low, (a ^ h) + (b ^ g) - (c ^ f) + (d ^ e)}
}

type abiMixer struct{}

func (mixer *abiMixer) Mix(a, b, c, d, e, f, g, h int64) summary {
	return wideValue(a, b, c, d, e, f, g, h)
}

func abiRecord(data []byte) string {
	if len(data) != 17 {
		return "ERR:LEN"
	}
	if data[0] > 1 {
		return "ERR:MODE"
	}
	var values [8]int64
	for index := range values {
		values[index] = signedWord(data, 1+index*2)
	}
	a, b, c, d, e, f, g, h := values[0], values[1], values[2], values[3], values[4], values[5], values[6], values[7]
	var result summary
	if data[0] == 0 {
		result = wideValue(a, b, c, d, e, f, g, h)
	} else {
		result = (&abiMixer{}).Mix(a, b, c, d, e, f, g, h)
	}
	return fmt.Sprintf("%d|%d|%d", result.weighted, result.spread, result.signature)
}

type policy interface{ Apply(int64) int64 }
type flatPolicy struct{}
type tieredPolicy struct{}
type rebatePolicy struct{}

func (flatPolicy) Apply(value int64) int64 { return value*3 + 7 }
func (*tieredPolicy) Apply(value int64) int64 {
	if value > 100 {
		return 500 + (value-100)*2
	}
	return value * 5
}
func (rebatePolicy) Apply(value int64) int64 {
	if value-13 < -20 {
		return -20
	}
	return value - 13
}

func applyPolicy(data []byte) string {
	if len(data) == 0 || (len(data)-1)%2 != 0 {
		return "ERR:LEN"
	}
	var selected policy
	switch data[0] {
	case 0:
		selected = flatPolicy{}
	case 1:
		selected = &tieredPolicy{}
	case 2:
		selected = rebatePolicy{}
	default:
		return "ERR:MODE"
	}
	results := make([]int64, 0, (len(data)-1)/2)
	var total int64
	for offset := 1; offset < len(data); offset += 2 {
		value := selected.Apply(signedWord(data, offset))
		results = append(results, value)
		total += value
	}
	return fmt.Sprintf("%d|%s", total, joinNumbers(results))
}

func reconstructed(data []byte) string {
	handlers := [...]func([]byte) string{wordCounts, ledger, rankRecords, schedule, abiRecord, applyPolicy}
	if len(data) == 0 || int(data[0]) >= len(handlers) {
		return "ERR:POINT"
	}
	return handlers[data[0]](data[1:])
}
