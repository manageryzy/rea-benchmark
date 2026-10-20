package main

import (
    "bufio"
    "encoding/hex"
    "fmt"
    "os"
)

//go:noinline
func score_record(b []byte) int64 {
    if len(b) < 6 { return -1 }
    if b[0] != 0x52 || b[1] != 0x45 { return -2 }
    count := uint32(b[3])
    if count > 32 || len(b) != 6 + int(count) { return -3 }
    acc := uint32(b[4]) | uint32(b[5]) << 8
    for i := uint32(0); i < count; i++ { acc = acc * 33 ^ (uint32(b[6+i]) + i) }
    switch b[2] {
    case 0: return int64(acc & 0xffff)
    case 1:
        if acc & 1 != 0 { return int64((acc >> 1) & 0xffff) }
        return int64((acc + 17) & 0xffff)
    case 2: return int64((acc ^ 0xa5a5) & 0xffff)
    default: return -4
    }
}

func main() {
    scanner := bufio.NewScanner(os.Stdin)
    writer := bufio.NewWriter(os.Stdout)
    defer writer.Flush()
    for scanner.Scan() {
        var b []byte
        if scanner.Text() != "-" { b, _ = hex.DecodeString(scanner.Text()) }
        fmt.Fprintln(writer, score_record(b))
    }
}
