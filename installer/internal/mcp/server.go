// Package mcp exposes only the typed installer operations, never a shell or file API.
// Stdio framing follows https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.
package mcp

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	"github.com/Lem0nTree/openlab/installer/internal/control"
)

type Handler func(context.Context, control.Request) (any, error)
type Tool struct {
	Name        string          `json:"name"`
	Description string          `json:"description"`
	InputSchema map[string]any  `json:"inputSchema"`
	Annotations map[string]bool `json:"annotations"`
}

func property(kind, description string, extra map[string]any) map[string]any {
	value := map[string]any{"type": kind, "description": description}
	for key, item := range extra {
		value[key] = item
	}
	return value
}
func Tools() []Tool {
	version := property("string", "Select latest or an exact published vMAJOR.MINOR.PATCH release.", map[string]any{"pattern": `^(latest|v[0-9]+\.[0-9]+\.[0-9]+)$`})
	deps := property("boolean", "Permit supported prerequisite package installation under the existing scoped grant.", nil)
	definitions := []struct {
		name, description string
		read, idempotent  bool
		properties        map[string]any
		required          []string
	}{
		{"inspect_host", "Inspect supported architecture, resources, Docker, Compose, and scheduler without changing the host.", true, true, map[string]any{}, nil},
		{"plan_install", "Plan a signed OpenLab installation and return the immutable plan id without applying it.", true, true, map[string]any{"version": version, "install_deps": deps}, nil},
		{"apply_install", "Apply a verified installation plan using the previously authorized scoped OpenLab helper.", false, true, map[string]any{"version": version, "install_deps": deps, "plan_id": property("string", "Use the exact id from plan_install; changed plans must be regenerated.", map[string]any{"pattern": "^[a-f0-9]{64}$"})}, []string{"plan_id"}},
		{"get_status", "Inspect redacted installation and service readiness without changing the installation.", true, true, map[string]any{}, nil},
		{"get_logs", "Retrieve a bounded, redacted log tail from one known OpenLab service.", true, true, map[string]any{"service": property("string", "Select a known Compose service.", map[string]any{"enum": []string{"openlab-server", "openlab-worker", "openlab-web", "postgres"}}), "lines": property("integer", "Limit returned log lines to at most 200.", map[string]any{"minimum": 1, "maximum": 200})}, []string{"service"}},
		{"restart_services", "Restart the fixed OpenLab services without deleting data or changing configuration.", false, false, map[string]any{}, nil},
		{"repair_installation", "Apply one enumerated repair to the existing installation without deleting volumes.", false, true, map[string]any{"repair": property("string", "Select a repair code reported by diagnostics.", map[string]any{"enum": []string{"worker", "migrations", "secrets"}})}, []string{"repair"}},
		{"configure_tailscale", "Configure optional Tailscale access and return any interactive browser authorization requirement.", false, true, map[string]any{"install_deps": deps}, nil},
		{"backup_installation", "Create a local installation backup in the fixed backup directory without exposing its contents.", false, false, map[string]any{}, nil},
		{"check_updates", "Check the signed release channel for newer eligible security updates without applying them.", true, true, map[string]any{}, nil},
		{"apply_security_update", "Apply an eligible signed security update after backup and compatibility checks, with image rollback on failure.", false, false, map[string]any{}, nil},
	}
	tools := make([]Tool, 0, len(definitions))
	for _, definition := range definitions {
		schema := map[string]any{"type": "object", "properties": definition.properties, "additionalProperties": false}
		if len(definition.required) > 0 {
			schema["required"] = definition.required
		}
		tools = append(tools, Tool{Name: definition.name, Description: definition.description, InputSchema: schema,
			Annotations: map[string]bool{"readOnlyHint": definition.read, "destructiveHint": !definition.read, "idempotentHint": definition.idempotent, "openWorldHint": !definition.read || definition.name == "plan_install" || definition.name == "check_updates"}})
	}
	return tools
}

func toolRequest(name string, arguments json.RawMessage) (control.Request, error) {
	actions := map[string]string{"inspect_host": "inspect", "plan_install": "plan", "apply_install": "install", "get_status": "status", "get_logs": "logs", "restart_services": "restart", "repair_installation": "repair", "configure_tailscale": "tailscale", "backup_installation": "backup", "check_updates": "check-updates", "apply_security_update": "update"}
	action, ok := actions[name]
	if !ok {
		return control.Request{}, errors.New("unknown tool")
	}
	if len(arguments) == 0 {
		arguments = []byte("{}")
	}
	var values map[string]json.RawMessage
	if err := control.DecodeStrict(arguments, &values); err != nil || values == nil {
		return control.Request{}, errors.New("arguments must be an object")
	}
	var definition Tool
	for _, tool := range Tools() {
		if tool.Name == name {
			definition = tool
			break
		}
	}
	allowed := definition.InputSchema["properties"].(map[string]any)
	for key, value := range values {
		if _, exists := allowed[key]; !exists {
			return control.Request{}, errors.New("unknown tool argument")
		}
		if strings.TrimSpace(string(value)) == "null" {
			return control.Request{}, errors.New("null arguments are not permitted")
		}
	}
	if required, ok := definition.InputSchema["required"].([]string); ok {
		for _, key := range required {
			if _, exists := values[key]; !exists {
				return control.Request{}, errors.New("missing required tool argument")
			}
		}
	}
	var request control.Request
	if err := control.DecodeStrict(arguments, &request); err != nil {
		return request, err
	}
	request.Action = action
	if _, explicit := values["lines"]; explicit && request.Lines < 1 {
		return request, errors.New("log line count must be positive")
	}
	if _, explicit := values["version"]; explicit && request.Version == "" {
		return request, errors.New("release version cannot be empty")
	}
	if request.Action == "logs" && request.Lines == 0 {
		request.Lines = 100
	}
	return request, request.Validate()
}

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params"`
}
type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id"`
	Result  any             `json:"result,omitempty"`
	Error   any             `json:"error,omitempty"`
}

func Serve(ctx context.Context, input io.Reader, output io.Writer, version string, handler Handler) error {
	scanner := bufio.NewScanner(input)
	scanner.Buffer(make([]byte, 4096), 65536)
	encoder := json.NewEncoder(output)
	var writeMutex sync.Mutex
	write := func(id json.RawMessage, result any, rpcError any) {
		writeMutex.Lock()
		defer writeMutex.Unlock()
		_ = encoder.Encode(rpcResponse{JSONRPC: "2.0", ID: id, Result: result, Error: rpcError})
	}
	errorResponse := func(id json.RawMessage, code int, message string) {
		write(id, nil, map[string]any{"code": code, "message": message})
	}
	initialized := false
	negotiated := false
	var pending sync.WaitGroup
	var activeMutex sync.Mutex
	active := map[string]context.CancelFunc{}
	// Serialize host operations. A queued call is cancellable; logs never share raw stdout.
	operationSlot := make(chan struct{}, 1)
	for scanner.Scan() {
		var request rpcRequest
		if err := control.DecodeStrict(scanner.Bytes(), &request); err != nil || request.JSONRPC != "2.0" || request.Method == "" {
			errorResponse(json.RawMessage("null"), -32600, "Invalid request")
			continue
		}
		notification := len(request.ID) == 0
		if !notification {
			var identifier any
			if json.Unmarshal(request.ID, &identifier) != nil {
				errorResponse(json.RawMessage("null"), -32600, "Invalid request id")
				continue
			}
			switch identifier.(type) {
			case string, float64:
			default:
				errorResponse(json.RawMessage("null"), -32600, "Request id must be a string or number")
				continue
			}
		}
		if notification {
			if request.Method == "notifications/initialized" && negotiated {
				initialized = true
			}
			if request.Method == "notifications/cancelled" {
				var params struct {
					RequestID json.RawMessage `json:"requestId"`
					Reason    string          `json:"reason"`
				}
				if json.Unmarshal(request.Params, &params) == nil {
					activeMutex.Lock()
					if cancel := active[string(params.RequestID)]; cancel != nil {
						cancel()
					}
					activeMutex.Unlock()
				}
			}
			continue
		}
		switch request.Method {
		case "initialize":
			var params struct {
				ProtocolVersion string          `json:"protocolVersion"`
				Capabilities    json.RawMessage `json:"capabilities"`
				ClientInfo      json.RawMessage `json:"clientInfo"`
			}
			if json.Unmarshal(request.Params, &params) != nil || params.ProtocolVersion == "" || negotiated {
				errorResponse(request.ID, -32602, "Invalid initialization")
				continue
			}
			protocol := "2025-11-25"
			if params.ProtocolVersion == "2025-03-26" || params.ProtocolVersion == "2025-06-18" {
				protocol = params.ProtocolVersion
			}
			negotiated = true
			write(request.ID, map[string]any{"protocolVersion": protocol, "capabilities": map[string]any{"tools": map[string]any{"listChanged": false}}, "serverInfo": map[string]string{"name": "openlab-installer", "version": version}, "instructions": "Install and diagnose OpenLab only. Mutations require a pre-existing scoped host grant. Owner passwords, API keys, setup tokens, arbitrary commands and paths are never accepted or returned."}, nil)
		case "ping":
			write(request.ID, map[string]any{}, nil)
		case "tools/list":
			if !initialized {
				errorResponse(request.ID, -32002, "Initialize the MCP session first")
				continue
			}
			write(request.ID, map[string]any{"tools": Tools()}, nil)
		case "tools/call":
			if !initialized {
				errorResponse(request.ID, -32002, "Initialize the MCP session first")
				continue
			}
			var params struct {
				Name      string          `json:"name"`
				Arguments json.RawMessage `json:"arguments"`
				Meta      json.RawMessage `json:"_meta,omitempty"`
			}
			if control.DecodeStrict(request.Params, &params) != nil {
				errorResponse(request.ID, -32602, "Invalid tool parameters")
				continue
			}
			action, err := toolRequest(params.Name, params.Arguments)
			if err != nil {
				errorResponse(request.ID, -32602, "Invalid tool or arguments")
				continue
			}
			callCtx, cancel := context.WithTimeout(ctx, 20*time.Minute)
			activeMutex.Lock()
			if len(active) >= 8 {
				activeMutex.Unlock()
				cancel()
				errorResponse(request.ID, -32000, "Too many pending tool calls")
				continue
			}
			if _, exists := active[string(request.ID)]; exists {
				activeMutex.Unlock()
				cancel()
				errorResponse(request.ID, -32600, "Duplicate active request id")
				continue
			}
			active[string(request.ID)] = cancel
			activeMutex.Unlock()
			pending.Add(1)
			go func(id json.RawMessage, action control.Request) {
				defer pending.Done()
				defer cancel()
				defer func() { activeMutex.Lock(); delete(active, string(id)); activeMutex.Unlock() }()
				var result any
				var err error
				select {
				case operationSlot <- struct{}{}:
					result, err = handler(callCtx, action)
					<-operationSlot
				case <-callCtx.Done():
					err = control.Fail("CANCELLED", "The operation was cancelled before it could complete.", "Inspect status before retrying.")
				}
				failed := err != nil
				if failed {
					result = control.SafeError(err)
				}
				structured, marshalErr := json.Marshal(result)
				if marshalErr != nil || len(structured) > 65536 {
					failed = true
					result = control.Failure{Code: "OUTPUT_LIMIT", Message: "The result exceeded the safe output limit."}
					structured, _ = json.Marshal(result)
				}
				write(id, map[string]any{"isError": failed, "content": []map[string]string{{"type": "text", "text": string(structured)}}, "structuredContent": map[string]any{"result": result}}, nil)
			}(append(json.RawMessage{}, request.ID...), action)
		default:
			errorResponse(request.ID, -32601, "Method not found")
		}
	}
	pending.Wait()
	if err := scanner.Err(); err != nil {
		return fmt.Errorf("MCP input exceeded framing limits or could not be read")
	}
	return nil
}
