package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Lem0nTree/openlab/installer/internal/control"
)

func TestPackageSignVerifyAndTamper(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	out := t.TempDir()
	for _, arch := range []string{"amd64", "arm64"} {
		if err = os.WriteFile(filepath.Join(out, "openlabctl-linux-"+arch), []byte(strings.Repeat("binary-fixture", 100)), 0600); err != nil {
			t.Fatal(err)
		}
	}
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	encoded := base64.StdEncoding.EncodeToString(public)
	t.Setenv("OPENLAB_RELEASE_SIGNING_SEED", base64.StdEncoding.EncodeToString(private.Seed()))
	t.Setenv("OPENLAB_RELEASE_PUBLIC_KEY", encoded)
	originalFlags, originalArgs := flag.CommandLine, os.Args
	defer func() { flag.CommandLine = originalFlags; os.Args = originalArgs }()
	flag.CommandLine = flag.NewFlagSet("releasepack-test", flag.ContinueOnError)
	hash := strings.Repeat("a", 64)
	os.Args = []string{"releasepack", "--root", root, "--out", out, "--metadata", filepath.Join(root, "installer/release-policy.json"), "--version", "v0.2.0", "--server", "ghcr.io/lem0ntree/openlab-server@sha256:" + hash, "--web", "ghcr.io/lem0ntree/openlab-web@sha256:" + hash, "--postgres", "pgvector/pgvector@sha256:" + hash}
	if err = run(); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(filepath.Join(out, "release.json"))
	sig, _ := os.ReadFile(filepath.Join(out, "release.json.sig"))
	manifest, err := control.VerifyManifest(data, sig, encoded)
	if err != nil {
		t.Fatal(err)
	}
	bundle, _ := os.ReadFile(filepath.Join(out, "openlab-bundle.tar.gz"))
	if _, err = control.ExtractBundle(bundle, manifest.BundleSHA256); err != nil {
		t.Fatal(err)
	}
	bootstrap, _ := os.ReadFile(filepath.Join(out, "install.sh"))
	if strings.Contains(string(bootstrap), "@PUBLIC_KEY_PEM@") || strings.Contains(string(bootstrap), "@VERSION@") || !strings.Contains(string(bootstrap), "BEGIN PUBLIC KEY") {
		t.Fatal("bootstrap trust root not rendered")
	}
	if output, err := exec.Command("sh", "-n", filepath.Join(out, "install.sh")).CombinedOutput(); err != nil {
		t.Fatalf("bootstrap shell syntax: %v %s", err, output)
	}
	if _, err := exec.LookPath("openssl"); err == nil {
		signatureFile := filepath.Join(out, "signature")
		if output, err := exec.Command("openssl", "base64", "-d", "-A", "-in", filepath.Join(out, "release.json.sig"), "-out", signatureFile).CombinedOutput(); err != nil {
			t.Fatalf("signature decode: %v %s", err, output)
		}
		if output, err := exec.Command("openssl", "pkeyutl", "-verify", "-pubin", "-inkey", filepath.Join(out, "release-public-key.pem"), "-rawin", "-in", filepath.Join(out, "release.json"), "-sigfile", signatureFile).CombinedOutput(); err != nil {
			t.Fatalf("bootstrap OpenSSL verification: %v %s", err, output)
		}
	}
	data[0] ^= 1
	if _, err = control.VerifyManifest(data, sig, encoded); err == nil {
		t.Fatal("tampered metadata verified")
	}
}
