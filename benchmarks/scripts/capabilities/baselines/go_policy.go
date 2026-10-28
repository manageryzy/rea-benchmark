// Inspect the parsed candidate without executing its declarations or imports.
package main

import (
    "encoding/json"
    "go/parser"
    "go/token"
    "io"
    "os"
    "strconv"
    "strings"
)

func main() {
    result := struct {
        Status string `json:"status"`
        Imports []string `json:"imports"`
    }{Status: "ok", Imports: []string{}}
    allowed := map[string]bool{
        "unsafe": true, "encoding/binary": true, "math": true, "math/bits": true,
        "sort": true, "slices": true, "cmp": true,
    }
    source, err := io.ReadAll(io.LimitReader(os.Stdin, 262145))
    if err != nil || len(source) == 0 || len(source) > 262144 {
        result.Status = "source_limit"
    } else {
        file, parseErr := parser.ParseFile(token.NewFileSet(), "solution.go", source, parser.AllErrors|parser.ParseComments)
        if parseErr != nil {
            result.Status = "parse_error"
        } else if file.Name.Name != "main" {
            result.Status = "wrong_package"
        } else {
            for _, spec := range file.Imports {
                path, unquoteErr := strconv.Unquote(spec.Path.Value)
                if unquoteErr != nil {
                    result.Status = "parse_error"
                    break
                }
                result.Imports = append(result.Imports, path)
                if !allowed[path] {
                    result.Status = "forbidden_import"
                }
            }
            for _, group := range file.Comments {
                for _, comment := range group.List {
                    text := strings.TrimSpace(comment.Text)
                    if strings.HasPrefix(text, "//go:") || strings.HasPrefix(text, "//export ") || strings.HasPrefix(text, "//line ") {
                        result.Status = "forbidden_directive"
                    }
                }
            }
        }
    }
    json.NewEncoder(os.Stdout).Encode(result)
}
