package control

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type setupRunner struct {
	output string
	err    error
	calls  []string
}

func (r *setupRunner) Run(_ context.Context, _ time.Duration, _ []byte, name string, args ...string) ([]byte, error) {
	r.calls = append(r.calls, name+" "+strings.Join(args, " "))
	return []byte(r.output), r.err
}
func TestTailscaleDetectionDoesNotConfuseUnavailableWithAbsent(t *testing.T) {
	for _, tc := range []struct {
		output string
		err    error
		want   string
	}{
		{`{"BackendState":"Running","Self":{"Online":true}}`, nil, "connected"},
		{`{"BackendState":"Running","Self":{"Online":false}}`, nil, "unavailable"},
		{`{"BackendState":"NeedsLogin"}`, nil, "needs_authorization"},
		{"", Fail("DEPENDENCY_MISSING", "missing", ""), "not_installed"},
		{"", errors.New("daemon stopped"), "unavailable"},
		{"not JSON", nil, "unavailable"},
	} {
		r := &setupRunner{output: tc.output, err: tc.err}
		e := Engine{Runner: r}
		if got := e.tailscaleState(context.Background()); got != tc.want {
			t.Fatalf("got %s want %s", got, tc.want)
		}
		if len(r.calls) != 1 || r.calls[0] != "tailscale status --json" {
			t.Fatal(r.calls)
		}
	}
}
func TestSetupRejectsExpandedExpiredAndSymlinkRequests(t *testing.T) {
	request := SetupRequest{ID: strings.Repeat("a", 32), Action: "kicad", RequestedAt: time.Now().UTC()}
	if err := request.Validate(time.Now()); err != nil {
		t.Fatal(err)
	}
	for _, action := range []string{"sh", "update", "https;sh", "/etc/passwd"} {
		r := request
		r.Action = action
		if r.Validate(time.Now()) == nil {
			t.Fatal(action)
		}
	}
	if request.Validate(time.Now().Add(11*time.Minute)) == nil {
		t.Fatal("expired request accepted")
	}
	if request.Validate(time.Now().Add(-2*time.Minute)) == nil {
		t.Fatal("future request accepted")
	}
	data, _ := json.Marshal(request)
	if DecodeStrict([]byte(strings.TrimSuffix(string(data), "}")+`,"command":"sh"}`), &request) == nil {
		t.Fatal("arbitrary command accepted")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "request.json")
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link.json")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readSetupRequest(link); err == nil {
		t.Fatal("symlink accepted")
	}
	if _, err := readSetupRequest(dir); err == nil {
		t.Fatal("directory accepted")
	}
}
func TestKicadRefusesUnsignedOrMismatchedReleaseBeforeDocker(t *testing.T) {
	for _, images := range []Images{{}, {KicadWorker: "ghcr.io/lem0ntree/openlab-worker-kicad@sha256:" + strings.Repeat("a", 64)}} {
		r := &setupRunner{}
		e := Engine{Runner: r, fetchRelease: func(context.Context, string, string) (Manifest, []byte, error) {
			return Manifest{Images: images}, nil, nil
		}}
		if err := e.installKicad(context.Background(), Config{Version: "v1.0.4"}); SafeError(err).Code != "KICAD_RELEASE_REQUIRED" {
			t.Fatal(err)
		}
		if len(r.calls) != 0 {
			t.Fatal("Docker ran before verification")
		}
	}
}

type httpsSetupRunner struct {
	serve string
	calls []string
}

func (r *httpsSetupRunner) Run(_ context.Context, _ time.Duration, _ []byte, name string, args ...string) ([]byte, error) {
	call := name + " " + strings.Join(args, " ")
	r.calls = append(r.calls, call)
	if call == "tailscale status --json" {
		return []byte(`{"BackendState":"Running","Self":{"Online":true,"DNSName":"lab.example.ts.net."}}`), nil
	}
	if call == "tailscale serve status --json" {
		return []byte(r.serve), nil
	}
	return nil, errors.New("unexpected mutation")
}
func TestHTTPSNeverOverwritesExistingRoutesOrPublicFunnel(t *testing.T) {
	for _, existing := range []string{
		`{"TCP":{"443":{"HTTPS":true}},"Web":{"lab.example.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:8001"}}}}}`,
		`{"TCP":{"443":{"HTTPS":true}},"Web":{"lab.example.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3000"}}}},"AllowFunnel":{"lab.example.ts.net:443":true}}`,
		`{"TCP":{"22":{"TCPForward":"localhost:22"}}}`,
	} {
		r := &httpsSetupRunner{serve: existing}
		e := Engine{Runner: r}
		if _, err := e.enableSetupHTTPS(context.Background(), Config{Port: 3000}); SafeError(err).Code != "HTTPS_CONFIG_EXISTS" {
			t.Fatal(err)
		}
		for _, call := range r.calls {
			if strings.Contains(call, "--bg") || strings.Contains(call, "funnel") {
				t.Fatal("existing settings mutated")
			}
		}
	}
}
func TestKiCadManifestRejectsMutableOrUnapprovedImages(t *testing.T) {
	m := testManifest()
	for _, image := range []string{"evil/worker@sha256:" + strings.Repeat("a", 64), "ghcr.io/lem0ntree/openlab-worker-kicad:latest"} {
		m.Images.KicadWorker = image
		if m.Validate() == nil {
			t.Fatal(image)
		}
	}
	m.Images.KicadWorker = "ghcr.io/lem0ntree/openlab-worker-kicad@sha256:" + strings.Repeat("a", 64)
	if err := m.Validate(); err != nil {
		t.Fatal(err)
	}
}
