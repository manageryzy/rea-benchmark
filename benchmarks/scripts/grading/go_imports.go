// Read source on stdin and inspect the syntax tree, never execute candidate code.
package main

import (
	"encoding/json"
	"go/parser"
	"go/token"
	"io"
	"os"
	"strconv"
)

func main() {
	result := struct {
		Status  string   `json:"status"`
		Imports []string `json:"imports"`
	}{Status: "ok", Imports: []string{}}
	source, err := io.ReadAll(io.LimitReader(os.Stdin, 4*1024*1024+1))
	if err != nil || len(source) > 4*1024*1024 {
		result.Status = "source_limit"
	} else {
		file, parseErr := parser.ParseFile(token.NewFileSet(), "candidate.go", source, parser.AllErrors)
		if parseErr != nil {
			result.Status = "parse_error"
		} else {
			for _, spec := range file.Imports {
				path, unquoteErr := strconv.Unquote(spec.Path.Value)
				if unquoteErr != nil {
					result.Status = "parse_error"
					break
				}
				result.Imports = append(result.Imports, path)
				if path == "unsafe" || path == "C" {
					result.Status = "forbidden_import"
				}
			}
		}
	}
	json.NewEncoder(os.Stdout).Encode(result)
}
