package main

import (
	"encoding/json"
	"os"
	"testing"
)

func TestMCPConfigIsValidJSON(t *testing.T) {
	original := os.Args
	defer func() { os.Args = original }()
	os.Args = []string{"openlabctl", "mcp", "print-config"}
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout := os.Stdout
	os.Stdout = writer
	code := run()
	writer.Close()
	os.Stdout = stdout
	data := make([]byte, 4096)
	count, _ := reader.Read(data)
	reader.Close()
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	var value map[string]any
	if json.Unmarshal(data[:count], &value) != nil {
		t.Fatalf("invalid config: %s", data[:count])
	}
}
