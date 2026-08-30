package control

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func testManifest() Manifest {
	digest := strings.Repeat("a", 64)
	return Manifest{SchemaVersion: 1, Version: "v0.2.0", PublishedAt: time.Now().UTC(), Architectures: []string{"arm64", "amd64"},
		Images:       Images{Server: "ghcr.io/lem0ntree/openlab-server@sha256:" + digest, Web: "ghcr.io/lem0ntree/openlab-web@sha256:" + digest, Postgres: "pgvector/pgvector@sha256:" + digest},
		BundleSHA256: digest, Binaries: map[string]string{"openlabctl-linux-amd64": digest, "openlabctl-linux-arm64": digest}, Classification: "security", UnattendedSafe: true, SchemaRevision: "0010_installation_onboarding", MinimumInstaller: "v0.2.0", RollbackCompatibleSchemas: []string{"0010_installation_onboarding"}}
}

func TestReleaseSignatureFailsClosed(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	data, _ := json.Marshal(testManifest())
	signature := []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(priv, data)))
	key := base64.StdEncoding.EncodeToString(pub)
	if _, err := VerifyManifest(data, signature, key); err != nil {
		t.Fatal(err)
	}
	data[len(data)-2] ^= 1
	if _, err := VerifyManifest(data, signature, key); err == nil {
		t.Fatal("tampered manifest accepted")
	}
	if _, err := VerifyManifest(data, signature, ""); err == nil {
		t.Fatal("missing trust root accepted")
	}
}

func TestManifestRejectsMutableOrUnapprovedImages(t *testing.T) {
	for _, image := range []string{"ghcr.io/lem0ntree/openlab-server:latest", "attacker.invalid/root@sha256:" + strings.Repeat("a", 64), "ghcr.io/lem0ntree/openlab-server@sha256:short"} {
		m := testManifest()
		m.Images.Server = image
		if m.Validate() == nil {
			t.Fatalf("accepted %s", image)
		}
	}
}

func TestRequestsRejectShellAndPathInputs(t *testing.T) {
	for _, payload := range []string{`{"action":"shell"}`, `{"action":"logs","service":";sh"}`, `{"action":"repair","repair":"../../etc"}`, `{"action":"install","version":"../../latest"}`, `{"action":"status","command":"whoami"}`, `{"action":"status"} {}`} {
		var request Request
		err := DecodeStrict([]byte(payload), &request)
		if err == nil {
			err = request.Validate()
		}
		if err == nil {
			t.Fatalf("accepted unsafe request %s", payload)
		}
	}
}

func TestRequiredChecksAndPolicy(t *testing.T) {
	checks := []Check{NewCheck("worker", "Worker", true, false, "WORKER_UNAVAILABLE", "Missing", "restart")}
	if Summarize(checks, "test").Overall != "blocked" {
		t.Fatal("failed worker passed")
	}
	checks[0].Required = false
	if Summarize(checks, "test").Overall != "ready_with_warnings" {
		t.Fatal("optional failure blocked")
	}
	if (Policy{Hour: 24}).Validate() == nil {
		t.Fatal("invalid schedule accepted")
	}
	sunday := time.Date(2026, 8, 30, 3, 0, 0, 0, time.UTC)
	if !DefaultPolicy().Due(sunday) || DefaultPolicy().Due(sunday.Add(time.Minute)) {
		t.Fatal("maintenance window mismatch")
	}
}

func TestRedaction(t *testing.T) {
	input := "OPENLAB_SECRET_KEY=abc123\nAuthorization: Bearer secret123\nhttp://name:pass@server\nOpenLab one-time owner setup token: token123\nknown-private-value"
	output := Redact(input, []string{"known-private-value"})
	for _, secret := range []string{"abc123", "secret123", "name:pass", "token123", "known-private-value"} {
		if strings.Contains(output, secret) {
			t.Fatalf("secret leaked: %s", secret)
		}
	}
}

func TestVersionComparison(t *testing.T) {
	if !Newer("v1.10.0", "v1.9.0") || Newer("v1.0.0", "v1.0.0") || Newer("latest", "v1.0.0") {
		t.Fatal("invalid version comparison")
	}
}
