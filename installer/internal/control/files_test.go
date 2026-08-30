package control

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestSecretsAreStableAndMissingOldSecretsFailClosed(t *testing.T) {
	fresh, err := EnsureSecrets(map[string]string{}, false)
	if err != nil {
		t.Fatal(err)
	}
	again, err := EnsureSecrets(fresh, true)
	if err != nil {
		t.Fatal(err)
	}
	for key, value := range fresh {
		if again[key] != value {
			t.Fatalf("rotated %s", key)
		}
	}
	delete(again, "OPENLAB_ENCRYPTION_KEY")
	if _, err = EnsureSecrets(again, true); err == nil {
		t.Fatal("regenerated missing key for existing data")
	}
}

func TestEnvironmentCannotInjectComposeOrCommands(t *testing.T) {
	for _, data := range []string{"COMPOSE_FILE=/tmp/evil.yml", "DOCKER_HOST=tcp://evil", "OPENLAB_SECRET_KEY=$(id)", "POSTGRES_DB=one\nPOSTGRES_DB=two"} {
		if _, err := ParseEnv([]byte(data)); err == nil {
			t.Fatalf("accepted %q", data)
		}
	}
	values, err := ParseEnv([]byte("# retained\nPOSTGRES_DB=openlab\nOPENLAB_PUBLIC_URL='http://lab.local:3000'\n"))
	if err != nil {
		t.Fatal(err)
	}
	if values["OPENLAB_PUBLIC_URL"] != "http://lab.local:3000" {
		t.Fatal("literal parsing failed")
	}
}

func TestAtomicWriteRefusesSymlink(t *testing.T) {
	root := t.TempDir()
	original := filepath.Join(root, "original")
	link := filepath.Join(root, "link")
	if err := os.WriteFile(original, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(original, link); err != nil {
		t.Skip(err)
	}
	if AtomicWrite(link, []byte("replace"), 0600) == nil {
		t.Fatal("followed symlink")
	}
	content, _ := os.ReadFile(original)
	if string(content) != "keep" {
		t.Fatal("original changed")
	}
}

func TestBundleRejectsTraversalAndSymlinks(t *testing.T) {
	for _, name := range []string{"../../etc/shadow", "/etc/shadow", "deploy/compose.yml"} {
		var buffer bytes.Buffer
		gz := gzip.NewWriter(&buffer)
		archive := tar.NewWriter(gz)
		kind := byte(tar.TypeReg)
		if name == "deploy/compose.yml" {
			kind = tar.TypeSymlink
		}
		_ = archive.WriteHeader(&tar.Header{Name: name, Typeflag: kind, Linkname: "/etc/shadow", Mode: 0600})
		_ = archive.Close()
		_ = gz.Close()
		data := buffer.Bytes()
		sum := sha256.Sum256(data)
		if _, err := ExtractBundle(data, fmt.Sprintf("%x", sum)); err == nil {
			t.Fatalf("unsafe archive accepted %s", name)
		}
	}
}
