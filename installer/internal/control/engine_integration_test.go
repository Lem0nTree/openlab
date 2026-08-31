//go:build integration

package control

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
	"time"
)

// These tests use real fixed-path root-owned state in a disposable container,
// with fault-injected Docker/network boundaries. They never mount a Docker socket.
type lifecycleRunner struct {
	calls    []string
	volume   bool
	failDump bool
	failStop bool
}

func (r *lifecycleRunner) Run(_ context.Context, _ time.Duration, _ []byte, name string, args ...string) ([]byte, error) {
	joined := strings.Join(args, " ")
	r.calls = append(r.calls, name+" "+joined)
	if name != "docker" {
		return nil, errors.New("unexpected host executable")
	}
	switch {
	case strings.HasPrefix(joined, "info "):
		return []byte("29.1.3"), nil
	case joined == "compose version --short":
		return []byte("2.40.0"), nil
	case strings.HasPrefix(joined, "volume ls"):
		return nil, nil
	case strings.HasPrefix(joined, "volume inspect"):
		if r.volume {
			return []byte("[]"), nil
		}
		return nil, errors.New("volume absent")
	case strings.Contains(joined, " ps --quiet postgres"):
		return []byte(strings.Repeat("a", 64)), nil
	case strings.Contains(joined, " stop ") && r.failStop:
		return nil, errors.New("injected partial stop")
	case strings.Contains(joined, " cp "):
		return nil, os.MkdirAll(args[len(args)-1], 0700)
	}
	if strings.Contains(joined, " down") || strings.Contains(joined, " rm") || strings.Contains(joined, "prune") {
		return nil, errors.New("destructive command escaped lifecycle")
	}
	return nil, nil
}
func (r *lifecycleRunner) RunTo(_ context.Context, _ time.Duration, output io.Writer, name string, args ...string) error {
	r.calls = append(r.calls, name+" "+strings.Join(args, " "))
	if r.failDump {
		return errors.New("injected dump failure")
	}
	_, err := output.Write([]byte("PGDMP-disposable-test"))
	return err
}

func TestDisposableLifecycle(t *testing.T) {
	if os.Getenv("OPENLAB_DISPOSABLE_TEST") != "1" || os.Geteuid() != 0 {
		t.Skip("requires explicit disposable root container")
	}
	if _, err := os.Stat("/.dockerenv"); err != nil {
		t.Fatal("refusing non-container integration test")
	}
	for _, path := range []string{AppRoot, ConfigRoot, StateRoot} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			t.Fatalf("refusing pre-existing installation path %s", path)
		}
	}
	var buffer bytes.Buffer
	gzipWriter := gzip.NewWriter(&buffer)
	archive := tar.NewWriter(gzipWriter)
	for _, name := range []string{"deploy/compose.yml", "deploy/compose.installer.yml"} {
		content := []byte("services: {}\n")
		if err := archive.WriteHeader(&tar.Header{Name: name, Mode: 0600, Size: int64(len(content)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		if _, err := archive.Write(content); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gzipWriter.Close(); err != nil {
		t.Fatal(err)
	}
	bundle := buffer.Bytes()
	hash := sha256.Sum256(bundle)
	public, private, _ := ed25519.GenerateKey(rand.Reader)
	manifest := testManifest()
	manifest.Images.KicadWorker = "ghcr.io/lem0ntree/openlab-worker-kicad@sha256:" + strings.Repeat("d", 64)
	manifest.BundleSHA256 = hex.EncodeToString(hash[:])
	manifest.RollbackCompatibleSchemas = []string{manifest.SchemaRevision}
	runner := &lifecycleRunner{}
	engine := &Engine{Version: "v0.2.0", PublicKey: base64.StdEncoding.EncodeToString(public), Runner: runner}
	engine.fetchRelease = func(_ context.Context, version, key string) (Manifest, []byte, error) {
		data, _ := json.Marshal(manifest)
		signature := []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(private, data)))
		verified, err := VerifyManifest(data, signature, key)
		return verified, data, err
	}
	engine.downloadAsset = func(_ context.Context, _ string, _ int64) ([]byte, error) { return bundle, nil }
	engine.probeReady = func(_ context.Context) (Report, error) {
		config, err := loadConfig()
		return Summarize([]Check{}, config.Version), err
	}
	ctx := context.Background()
	plan, _, err := engine.Plan(ctx, Request{Action: "plan", Version: "v0.2.0"})
	if err != nil {
		t.Fatal(err)
	}
	request := Request{Action: "install", Version: plan.Version, PlanID: plan.ID}
	if _, err = engine.Handle(ctx, request); err != nil {
		t.Fatal(err)
	}
	before, err := readEnvironment()
	if err != nil {
		t.Fatal(err)
	}
	if before["OPENLAB_ENCRYPTION_KEY"] == "" {
		t.Fatal("encryption not bootstrapped")
	}
	runner.volume = true
	t.Run("repeat preserves secrets", func(t *testing.T) {
		plan, _, err := engine.Plan(ctx, Request{Action: "plan", Version: manifest.Version})
		if err != nil {
			t.Fatal(err)
		}
		request.PlanID = plan.ID
		if _, err = engine.Handle(ctx, request); err != nil {
			t.Fatal(err)
		}
		after, _ := readEnvironment()
		for _, key := range []string{"OPENLAB_SECRET_KEY", "OPENLAB_ENCRYPTION_KEY", "OPENLAB_SETUP_TOKEN", "POSTGRES_PASSWORD"} {
			if before[key] != after[key] {
				t.Fatalf("rotated %s", key)
			}
		}
	})
	t.Run("changed plan refuses before mutation", func(t *testing.T) {
		count := len(runner.calls)
		request.PlanID = strings.Repeat("0", 64)
		if _, err := engine.Handle(ctx, request); SafeError(err).Code != "PLAN_CHANGED" {
			t.Fatal("stale plan accepted", err)
		}
		for _, call := range runner.calls[count:] {
			if strings.Contains(call, " up ") || strings.Contains(call, " pull") {
				t.Fatal("stale plan mutated services")
			}
		}
	})
	t.Run("partial stop attempts service recovery", func(t *testing.T) {
		runner.failStop = true
		defer func() { runner.failStop = false }()
		count := len(runner.calls)
		if _, err := engine.Handle(ctx, Request{Action: "backup"}); err == nil {
			t.Fatal("partial stop accepted")
		}
		calls := strings.Join(runner.calls[count:], "\n")
		if !strings.Contains(calls, " up -d --no-build") {
			t.Fatal("services not restarted after partial stop")
		}
	})
	t.Run("failed dump has no complete receipt", func(t *testing.T) {
		runner.failDump = true
		defer func() { runner.failDump = false }()
		if _, err := engine.Handle(ctx, Request{Action: "backup"}); err == nil {
			t.Fatal("failed dump accepted")
		}
		entries, _ := os.ReadDir(StateRoot + "/backups")
		for _, entry := range entries {
			if _, err := os.Stat(StateRoot + "/backups/" + entry.Name() + "/receipt.json"); err == nil {
				t.Fatal("incomplete backup reported complete")
			}
		}
	})
	t.Run("KiCad readiness failure restores the previous worker", func(t *testing.T) {
		config, _ := loadConfig()
		previous, _ := trustedFile(ConfigRoot + "/openlab.env")
		probe := engine.probeReady
		engine.probeReady = func(context.Context) (Report, error) { return Report{}, errors.New("injected KiCad readiness failure") }
		defer func() { engine.probeReady = probe }()
		if err := engine.installKicad(ctx, config); SafeError(err).Code != "KICAD_INSTALL_FAILED" {
			t.Fatal(err)
		}
		after, _ := trustedFile(ConfigRoot + "/openlab.env")
		current, _ := loadConfig()
		if !bytes.Equal(previous, after) || current.KicadEnabled {
			t.Fatal("failed KiCad activation changed configuration")
		}
	})
	t.Run("owner setup selects signed KiCad worker and preserves secrets", func(t *testing.T) {
		request := SetupRequest{ID: strings.Repeat("a", 32), Action: "kicad", RequestedAt: time.Now().UTC()}
		data, _ := json.Marshal(request)
		if err := AtomicWrite(StateRoot+"/control/policy/setup-request.json", data, 0600); err != nil {
			t.Fatal(err)
		}
		result, err := engine.ProcessSetup(ctx)
		if err != nil {
			t.Fatal(err)
		}
		status := result.(SetupStatus)
		if status.Operation.Status != "completed" {
			t.Fatal(status.Operation)
		}
		current, _ := loadConfig()
		values, _ := readEnvironment()
		if !current.KicadEnabled || values["OPENLAB_WORKER_IMAGE"] != manifest.Images.KicadWorker || values["OPENLAB_SERVER_IMAGE"] != manifest.Images.Server {
			t.Fatal("wrong image activated")
		}
		if values["OPENLAB_ENCRYPTION_KEY"] != before["OPENLAB_ENCRYPTION_KEY"] {
			t.Fatal("secrets changed")
		}
		// Replaying the same request must not run Docker again.
		if err := AtomicWrite(StateRoot+"/control/policy/setup-request.json", data, 0600); err != nil {
			t.Fatal(err)
		}
		count := len(runner.calls)
		if _, err := engine.ProcessSetup(ctx); err == nil {
			t.Fatal("replay accepted")
		}
		for _, call := range runner.calls[count:] {
			if strings.HasPrefix(call, "docker ") {
				t.Fatal("replay ran Docker")
			}
		}
	})
	t.Run("manual feature gate", func(t *testing.T) {
		manifest.Version = "v0.3.0"
		manifest.Classification = "feature"
		manifest.UnattendedSafe = false
		count := len(runner.calls)
		if _, err := engine.Handle(ctx, Request{Action: "update"}); SafeError(err).Code != "MANUAL_UPDATE_REQUIRED" {
			t.Fatal(err)
		}
		if len(runner.calls) != count {
			t.Fatal("ineligible update changed host")
		}
	})
	t.Run("readiness failure rolls back images not database", func(t *testing.T) {
		probes := 0
		engine.probeReady = func(_ context.Context) (Report, error) {
			probes++
			config, _ := loadConfig()
			report := Summarize([]Check{}, config.Version)
			if probes == 1 {
				return report, Fail("READINESS_TIMEOUT", "Injected startup failure", "")
			}
			return report, nil
		}
		result, err := engine.Handle(ctx, Request{Action: "update", ManualFeature: true})
		if SafeError(err).Code != "UPDATE_ROLLED_BACK" {
			t.Fatalf("rollback not reported: %v %v", result, err)
		}
		current, _ := loadConfig()
		if current.Version != "v0.2.0" {
			t.Fatal("old release identity not restored")
		}
		after, _ := readEnvironment()
		if before["OPENLAB_ENCRYPTION_KEY"] != after["OPENLAB_ENCRYPTION_KEY"] {
			t.Fatal("rollback rotated encryption")
		}
		for _, call := range runner.calls {
			if strings.Contains(call, "pg_restore") || strings.Contains(call, "downgrade") || strings.Contains(call, "volume rm") {
				t.Fatal("automatic destructive restore")
			}
		}
	})
	t.Run("compatible explicit feature update", func(t *testing.T) {
		engine.probeReady = func(_ context.Context) (Report, error) {
			config, _ := loadConfig()
			return Summarize([]Check{}, config.Version), nil
		}
		if _, err := engine.Handle(ctx, Request{Action: "update", ManualFeature: true}); err != nil {
			t.Fatal(err)
		}
		current, _ := loadConfig()
		if current.Version != "v0.3.0" {
			t.Fatal("new version not active")
		}
		if !current.KicadEnabled || current.WorkerImage() != manifest.Images.KicadWorker {
			t.Fatal("update removed KiCad")
		}
	})
	t.Run("helper protocol rejects expanded authority", func(t *testing.T) {
		var output bytes.Buffer
		err := engine.ServeHelper(ctx, strings.NewReader(`{"action":"status","command":"sh"}`), &output)
		if err == nil || !strings.Contains(output.String(), `"error"`) {
			t.Fatal("arbitrary command allowed")
		}
		if strings.Contains(output.String(), before["OPENLAB_SECRET_KEY"]) {
			t.Fatal("helper exposed a secret")
		}
	})
}
