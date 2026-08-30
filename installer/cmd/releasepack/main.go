// releasepack creates release artifacts in CI. It never generates production
// signing keys; a protected release environment supplies the existing seed.
package main

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Lem0nTree/openlab/installer/internal/control"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "release packaging failed:", err)
		os.Exit(1)
	}
}
func digest(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func run() error {
	root := flag.String("root", "..", "Repository root")
	out := flag.String("out", "dist", "Directory containing compiled release binaries")
	version := flag.String("version", "", "Release tag")
	server := flag.String("server", "", "Immutable multiarch server image")
	web := flag.String("web", "", "Immutable multiarch web image")
	postgres := flag.String("postgres", "", "Immutable multiarch pgvector image")
	metadata := flag.String("metadata", "release-policy.json", "Reviewed compatibility policy")
	flag.Parse()
	seed, err := base64.StdEncoding.DecodeString(os.Getenv("OPENLAB_RELEASE_SIGNING_SEED"))
	if err != nil || len(seed) != ed25519.SeedSize {
		return errors.New("a protected base64 Ed25519 seed is required")
	}
	key := ed25519.NewKeyFromSeed(seed)
	public := key.Public().(ed25519.PublicKey)
	if base64.StdEncoding.EncodeToString(public) != os.Getenv("OPENLAB_RELEASE_PUBLIC_KEY") {
		return errors.New("signing key does not match the configured installer trust root")
	}
	var policy struct {
		Classification            string   `json:"classification"`
		UnattendedSafe            bool     `json:"unattended_safe"`
		SchemaRevision            string   `json:"schema_revision"`
		MinimumInstaller          string   `json:"minimum_installer"`
		RollbackCompatibleSchemas []string `json:"rollback_compatible_schemas"`
	}
	raw, err := os.ReadFile(*metadata)
	if err != nil {
		return err
	}
	if err = control.DecodeStrict(raw, &policy); err != nil {
		return err
	}
	var bundle bytes.Buffer
	zipped := gzip.NewWriter(&bundle)
	archive := tar.NewWriter(zipped)
	for _, name := range []string{"deploy/compose.yml", "deploy/compose.installer.yml"} {
		content, err := os.ReadFile(filepath.Join(*root, name))
		if err != nil {
			return err
		}
		if err = archive.WriteHeader(&tar.Header{Name: name, Mode: 0600, Size: int64(len(content)), Typeflag: tar.TypeReg}); err != nil {
			return err
		}
		if _, err = archive.Write(content); err != nil {
			return err
		}
	}
	if err = archive.Close(); err != nil {
		return err
	}
	if err = zipped.Close(); err != nil {
		return err
	}
	manifest := control.Manifest{SchemaVersion: 1, Version: *version, PublishedAt: time.Now().UTC(), Architectures: []string{"amd64", "arm64"}, Images: control.Images{Server: *server, Web: *web, Postgres: *postgres}, BundleSHA256: digest(bundle.Bytes()), Binaries: map[string]string{}, Classification: policy.Classification, UnattendedSafe: policy.UnattendedSafe, SchemaRevision: policy.SchemaRevision, MinimumInstaller: policy.MinimumInstaller, RollbackCompatibleSchemas: policy.RollbackCompatibleSchemas}
	for _, arch := range manifest.Architectures {
		name := "openlabctl-linux-" + arch
		content, err := os.ReadFile(filepath.Join(*out, name))
		if err != nil {
			return err
		}
		if len(content) < 1024 {
			return errors.New("missing release binary")
		}
		manifest.Binaries[name] = digest(content)
	}
	if err = manifest.Validate(); err != nil {
		return err
	}
	serialized, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	signature := []byte(base64.StdEncoding.EncodeToString(ed25519.Sign(key, serialized)) + "\n")
	// Verify the artifacts through the same trust code used on installed hosts.
	if _, err = control.VerifyManifest(serialized, signature, base64.StdEncoding.EncodeToString(public)); err != nil {
		return err
	}
	if _, err = control.ExtractBundle(bundle.Bytes(), manifest.BundleSHA256); err != nil {
		return err
	}
	der, err := x509.MarshalPKIXPublicKey(public)
	if err != nil {
		return err
	}
	publicPEM := pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der})
	template, err := os.ReadFile(filepath.Join(*root, "installer/bootstrap.sh.in"))
	if err != nil {
		return err
	}
	bootstrap := strings.ReplaceAll(strings.ReplaceAll(string(template), "@PUBLIC_KEY_PEM@", strings.TrimSpace(string(publicPEM))), "@VERSION@", manifest.Version)
	artifacts := map[string][]byte{"release.json": serialized, "release.json.sig": signature, "openlab-bundle.tar.gz": bundle.Bytes(), "install.sh": []byte(bootstrap), "release-public-key.pem": publicPEM}
	for name, content := range artifacts {
		if err = os.WriteFile(filepath.Join(*out, name), content, 0644); err != nil {
			return err
		}
	}
	return nil
}
