package control

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

// AtomicWrite refuses symlinks and replaces only one explicit regular file.
func AtomicWrite(path string, content []byte, mode os.FileMode) error {
	path = filepath.Clean(path)
	if !filepath.IsAbs(path) {
		return errors.New("absolute path required")
	}
	for current := path; current != filepath.Dir(current); current = filepath.Dir(current) {
		info, err := os.Lstat(current)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		if err == nil && info.Mode()&os.ModeSymlink != 0 {
			return errors.New("symlink target refused")
		}
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".openlab-")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	if err = file.Chmod(mode); err != nil {
		file.Close()
		return err
	}
	if _, err = file.Write(content); err != nil {
		file.Close()
		return err
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if err = os.Rename(file.Name(), path); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err == nil {
		defer directory.Close()
		err = directory.Sync()
	}
	return err
}

var envKeys = map[string]bool{
	"POSTGRES_DB": true, "POSTGRES_USER": true, "POSTGRES_PASSWORD": true, "DATABASE_URL": true,
	"OPENLAB_DATA_DIR": true, "OPENLAB_SETUP_TOKEN": true, "OPENLAB_SECRET_KEY": true, "OPENLAB_ENCRYPTION_KEY": true,
	"OPENLAB_PUBLIC_URL": true, "OPENLAB_KICAD_CLI": true, "OPENLAB_SERVER_IMAGE": true, "OPENLAB_WORKER_IMAGE": true,
	"OPENLAB_WEB_IMAGE": true, "OPENLAB_POSTGRES_IMAGE": true, "OPENLAB_BIND_ADDRESS": true, "OPENLAB_PORT": true,
	"OPENLAB_VERSION": true, "OPENLAB_INSTALLER_CONTROL_DIR": true,
}

func ParseEnv(data []byte) (map[string]string, error) {
	if len(data) > 65536 {
		return nil, errors.New("environment file too large")
	}
	result := map[string]string{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		pair := strings.SplitN(line, "=", 2)
		if len(pair) != 2 {
			return nil, errors.New("invalid environment entry")
		}
		key := strings.TrimSpace(pair[0])
		value := strings.TrimSpace(pair[1])
		if !envKeys[key] {
			return nil, Fail("ENV_REVIEW_REQUIRED", "Environment contains unsupported keys; review adoption manually.", "Keep only documented OpenLab deployment settings.")
		}
		if _, exists := result[key]; exists {
			return nil, errors.New("duplicate environment key")
		}
		if len(value) >= 2 && ((value[0] == '\'' && value[len(value)-1] == '\'') || (value[0] == '"' && value[len(value)-1] == '"')) {
			value = value[1 : len(value)-1]
		}
		if strings.ContainsAny(value, "$\r\n\x00\"'") {
			return nil, Fail("ENV_INTERPOLATION_REFUSED", "Environment interpolation or embedded quoting requires manual review.", "Use literal deployment values.")
		}
		result[key] = value
	}
	return result, nil
}

func EncodeEnv(values map[string]string) ([]byte, error) {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var out strings.Builder
	out.WriteString("# Managed by openlabctl. Secrets must be backed up, never committed.\n")
	for _, key := range keys {
		if !envKeys[key] || strings.ContainsAny(values[key], "$\r\n\x00\"'") {
			return nil, errors.New("unsafe environment value")
		}
		out.WriteString(key + "=" + values[key] + "\n")
	}
	return []byte(out.String()), nil
}

func randomValue(size int) (string, error) {
	data := make([]byte, size)
	if _, err := rand.Read(data); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(data), nil
}

func EnsureSecrets(values map[string]string, databaseExists bool) (map[string]string, error) {
	result := map[string]string{}
	for key, value := range values {
		result[key] = value
	}
	missing := func(key string) bool { return result[key] == "" || strings.HasPrefix(result[key], "replace-with-") }
	for _, key := range []string{"POSTGRES_PASSWORD", "DATABASE_URL", "OPENLAB_SECRET_KEY", "OPENLAB_ENCRYPTION_KEY"} {
		if databaseExists && missing(key) {
			return nil, Fail("EXISTING_DATA_NEEDS_ENV", "Existing database data was found but required original secrets are missing.", "Restore the original environment file; no secrets were rotated.")
		}
	}
	for _, key := range []string{"OPENLAB_SECRET_KEY", "POSTGRES_PASSWORD", "OPENLAB_SETUP_TOKEN"} {
		if missing(key) {
			value, err := randomValue(48)
			if err != nil {
				return nil, err
			}
			result[key] = value
		}
	}
	if result["OPENLAB_ENCRYPTION_KEY"] == "" {
		data := make([]byte, 32)
		if _, err := rand.Read(data); err != nil {
			return nil, err
		}
		result["OPENLAB_ENCRYPTION_KEY"] = base64.URLEncoding.EncodeToString(data)
	}
	encryption, err := base64.URLEncoding.DecodeString(result["OPENLAB_ENCRYPTION_KEY"])
	if err != nil || len(encryption) != 32 {
		return nil, Fail("ENCRYPTION_KEY_INVALID", "The existing encryption key is invalid; it was not replaced.", "Restore the original encryption key from backup.")
	}
	for _, key := range []string{"POSTGRES_USER", "POSTGRES_DB"} {
		if result[key] == "" {
			result[key] = "openlab"
		}
		if !regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_]{0,62}$`).MatchString(result[key]) {
			return nil, errors.New("invalid PostgreSQL identifier")
		}
	}
	if missing("DATABASE_URL") || strings.Contains(result["DATABASE_URL"], "replace-with-a-unique-postgres-password") {
		result["DATABASE_URL"] = "postgresql+psycopg://" + result["POSTGRES_USER"] + ":" + result["POSTGRES_PASSWORD"] + "@postgres:5432/" + result["POSTGRES_DB"]
	}
	if len(result["OPENLAB_SECRET_KEY"]) < 32 {
		return nil, errors.New("session secret must contain at least 32 characters")
	}
	return result, nil
}

func ExtractBundle(data []byte, expected string) (map[string][]byte, error) {
	if err := VerifyDigest(data, expected); err != nil {
		return nil, err
	}
	zipped, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	defer zipped.Close()
	archive := tar.NewReader(zipped)
	files := map[string][]byte{}
	for {
		header, err := archive.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		if header.Name != "deploy/compose.yml" && header.Name != "deploy/compose.installer.yml" {
			return nil, errors.New("unexpected path in installation bundle")
		}
		if header.Typeflag != tar.TypeReg || header.Size < 0 || header.Size > 128*1024 || files[header.Name] != nil {
			return nil, errors.New("invalid bundle entry")
		}
		content, err := io.ReadAll(io.LimitReader(archive, 128*1024+1))
		if err != nil || len(content) > 128*1024 {
			return nil, errors.New("bundle file exceeds limit")
		}
		files[header.Name] = content
	}
	if len(files) != 2 {
		return nil, errors.New("installation bundle is incomplete")
	}
	return files, nil
}
