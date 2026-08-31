package control

import (
	"context"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const ReleaseBase = "https://github.com/Lem0nTree/openlab/releases"

type Images struct {
	Server      string `json:"server"`
	Web         string `json:"web"`
	Postgres    string `json:"postgres"`
	KicadWorker string `json:"kicad_worker,omitempty"`
}

type Manifest struct {
	SchemaVersion             int               `json:"schema_version"`
	Version                   string            `json:"version"`
	PublishedAt               time.Time         `json:"published_at"`
	Architectures             []string          `json:"architectures"`
	Images                    Images            `json:"images"`
	BundleSHA256              string            `json:"bundle_sha256"`
	Binaries                  map[string]string `json:"binaries"`
	Classification            string            `json:"classification"`
	UnattendedSafe            bool              `json:"unattended_safe"`
	SchemaRevision            string            `json:"schema_revision"`
	MinimumInstaller          string            `json:"minimum_installer"`
	RollbackCompatibleSchemas []string          `json:"rollback_compatible_schemas"`
}

var digestPattern = regexp.MustCompile(`^[a-f0-9]{64}$`)
var schemaPattern = regexp.MustCompile(`^[0-9]{4}_[a-z0-9_]{1,80}$`)

func (m Manifest) Validate() error {
	if m.SchemaVersion != 1 || !ValidRelease(m.Version) || !ValidRelease(m.MinimumInstaller) || m.PublishedAt.IsZero() || m.PublishedAt.After(time.Now().Add(5*time.Minute)) {
		return errors.New("invalid manifest version or date")
	}
	if m.Classification != "security" && m.Classification != "feature" {
		return errors.New("invalid release classification")
	}
	if !schemaPattern.MatchString(m.SchemaRevision) {
		return errors.New("missing schema revision")
	}
	if len(m.RollbackCompatibleSchemas) == 0 || len(m.RollbackCompatibleSchemas) > 32 {
		return errors.New("invalid compatibility set")
	}
	compatible := map[string]bool{}
	for _, schema := range m.RollbackCompatibleSchemas {
		if !schemaPattern.MatchString(schema) || compatible[schema] {
			return errors.New("invalid compatible schema")
		}
		compatible[schema] = true
	}
	if !digestPattern.MatchString(m.BundleSHA256) {
		return errors.New("invalid bundle digest")
	}
	if len(m.Architectures) != 2 {
		return errors.New("release must contain both supported architectures")
	}
	found := map[string]bool{}
	for _, arch := range m.Architectures {
		if (arch != "arm64" && arch != "amd64") || found[arch] || !digestPattern.MatchString(m.Binaries["openlabctl-linux-"+arch]) {
			return errors.New("invalid binary architecture or checksum")
		}
		found[arch] = true
	}
	for name, image := range map[string]string{"server": m.Images.Server, "web": m.Images.Web, "postgres": m.Images.Postgres} {
		prefix := "ghcr.io/lem0ntree/openlab-" + name + "@sha256:"
		if name == "postgres" {
			prefix = "pgvector/pgvector@sha256:"
		}
		if !strings.HasPrefix(image, prefix) || !digestPattern.MatchString(strings.TrimPrefix(image, prefix)) {
			return errors.New("images must use approved repositories and immutable digests")
		}
	}
	if m.Images.KicadWorker != "" {
		prefix := "ghcr.io/lem0ntree/openlab-worker-kicad@sha256:"
		if !strings.HasPrefix(m.Images.KicadWorker, prefix) || !digestPattern.MatchString(strings.TrimPrefix(m.Images.KicadWorker, prefix)) {
			return errors.New("KiCad worker must use the approved repository and an immutable digest")
		}
	}
	return nil
}

func VerifyManifest(data, signature []byte, encodedKey string) (Manifest, error) {
	var manifest Manifest
	key, err := base64.StdEncoding.DecodeString(strings.TrimSpace(encodedKey))
	if err != nil || len(key) != ed25519.PublicKeySize {
		return manifest, Fail("RELEASE_TRUST_UNCONFIGURED", "This installer has no valid release verification key.", "Use an official signed OpenLab installer release.")
	}
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(signature)))
	if err != nil || len(sig) != ed25519.SignatureSize || !ed25519.Verify(ed25519.PublicKey(key), data, sig) {
		return manifest, Fail("RELEASE_SIGNATURE_INVALID", "Release signature verification failed; nothing was applied.", "Retry with an official release; do not disable signature checks.")
	}
	if err := DecodeStrict(data, &manifest); err != nil {
		return manifest, err
	}
	return manifest, manifest.Validate()
}

func VerifyDigest(data []byte, expected string) error {
	sum := sha256.Sum256(data)
	if !digestPattern.MatchString(expected) || hex.EncodeToString(sum[:]) != expected {
		return Fail("ASSET_DIGEST_INVALID", "Downloaded asset checksum does not match the signed manifest.", "Retry the download.")
	}
	return nil
}

func releaseURL(version, asset string) (string, error) {
	if version != "latest" && !ValidRelease(version) {
		return "", errors.New("invalid release")
	}
	allowed := map[string]bool{"release.json": true, "release.json.sig": true, "openlab-bundle.tar.gz": true, "openlabctl-linux-arm64": true, "openlabctl-linux-amd64": true}
	if !allowed[asset] {
		return "", errors.New("invalid release asset")
	}
	if version == "latest" {
		return ReleaseBase + "/latest/download/" + asset, nil
	}
	return ReleaseBase + "/download/" + version + "/" + asset, nil
}

func downloadHostAllowed(target *url.URL) bool {
	if target.Scheme != "https" || target.User != nil || (target.Port() != "" && target.Port() != "443") {
		return false
	}
	switch target.Hostname() {
	case "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com":
		return true
	}
	return false
}

func Download(ctx context.Context, target string, limit int64) ([]byte, error) {
	parsed, err := url.Parse(target)
	if err != nil || !downloadHostAllowed(parsed) {
		return nil, errors.New("download host is not approved")
	}
	client := &http.Client{Timeout: 5 * time.Minute, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) > 5 || !downloadHostAllowed(req.URL) {
			return errors.New("download redirect refused")
		}
		return nil
	}}
	req, err := http.NewRequestWithContext(ctx, "GET", target, nil)
	if err != nil {
		return nil, err
	}
	response, err := client.Do(req)
	if err != nil {
		return nil, Fail("DOWNLOAD_FAILED", "Could not download the release asset.", "Check DNS, HTTPS connectivity, and release availability.")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, Fail("RELEASE_UNAVAILABLE", "The requested release asset is unavailable.", "Choose a published release version.")
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil || int64(len(data)) > limit {
		return nil, errors.New("release asset exceeds size limit or download failed")
	}
	return data, nil
}

func FetchManifest(ctx context.Context, version, key string) (Manifest, []byte, error) {
	if version == "" {
		version = "latest"
	}
	target, err := releaseURL(version, "release.json")
	if err != nil {
		return Manifest{}, nil, err
	}
	data, err := Download(ctx, target, 65536)
	if err != nil {
		return Manifest{}, nil, err
	}
	// Resolve the immutable version from untrusted metadata only to locate the signature;
	// no metadata is acted upon until signature and strict validation both succeed.
	var hint struct {
		Version string `json:"version"`
	}
	if err := jsonVersion(data, &hint.Version); err != nil {
		return Manifest{}, nil, err
	}
	if version != "latest" && hint.Version != version {
		return Manifest{}, nil, errors.New("release version mismatch")
	}
	sigURL, err := releaseURL(hint.Version, "release.json.sig")
	if err != nil {
		return Manifest{}, nil, err
	}
	signature, err := Download(ctx, sigURL, 1024)
	if err != nil {
		return Manifest{}, nil, err
	}
	manifest, err := VerifyManifest(data, signature, key)
	return manifest, data, err
}

func jsonVersion(data []byte, version *string) error {
	// Strict decode is performed after verification; extracting this one field cannot select a host or arbitrary path.
	var hint Manifest
	if err := DecodeStrict(data, &hint); err != nil {
		return err
	}
	if !ValidRelease(hint.Version) {
		return errors.New("invalid release version")
	}
	*version = hint.Version
	return nil
}
