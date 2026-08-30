package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/Lem0nTree/openlab/installer/internal/control"
)

func TestInitializationAndToolContract(t *testing.T) {
	input := `{"jsonrpc":"2.0","id":1,"method":"tools/list"}
{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":3,"method":"tools/list"}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_status","arguments":{}}}
`
	var output bytes.Buffer
	called := false
	err := Serve(context.Background(), strings.NewReader(input), &output, "v0.2.0", func(_ context.Context, request control.Request) (any, error) {
		called = true
		return map[string]string{"state": "ready"}, nil
	})
	if err != nil || !called {
		t.Fatal(err, "handler not invoked")
	}
	lines := strings.Split(strings.TrimSpace(output.String()), "\n")
	if len(lines) != 4 {
		t.Fatalf("unexpected protocol lines: %s", output.String())
	}
	for _, line := range lines {
		var response rpcResponse
		if json.Unmarshal([]byte(line), &response) != nil || response.JSONRPC != "2.0" {
			t.Fatal("non-protocol stdout")
		}
	}
	if !strings.Contains(lines[0], "Initialize") {
		t.Fatal("pre-initialization tools allowed")
	}
}

func TestToolArgumentsDoNotBroadenAuthority(t *testing.T) {
	cases := [][2]string{{"get_status", `{"action":"install"}`}, {"get_logs", `{"service":"/etc/shadow"}`}, {"repair_installation", `{"repair":"rm -rf /"}`}, {"apply_install", `{}`}, {"get_status", `null`}, {"get_status", `{"password":"secret"}`}}
	for _, test := range cases {
		if _, err := toolRequest(test[0], []byte(test[1])); err == nil {
			t.Fatalf("unsafe tool accepted %v", test)
		}
	}
}

func TestToolNamesUniqueAndSchemasClosed(t *testing.T) {
	seen := map[string]bool{}
	for _, tool := range Tools() {
		if seen[tool.Name] || len(tool.Description) < 10 || tool.InputSchema["additionalProperties"] != false {
			t.Fatalf("invalid tool %s", tool.Name)
		}
		seen[tool.Name] = true
	}
}
