package control

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

// Adopt is deliberately terminal-only. The local administrator chooses the env
// file and attests that the previous deployment controller has been paused.
// No legacy Compose file or script is executed with the installer grant.
func (e *Engine) Adopt(ctx context.Context, envPath, project, version string, handover bool) (any, error) {
	if os.Geteuid() != 0 || !handover {
		return nil, Fail("HANDOVER_REQUIRED", "Adoption requires a local administrator and an explicit controller handover.", "Pause the existing deployment automation, then run sudo openlabctl adopt --accept-handover --env-file PATH --project NAME.")
	}
	if !filepath.IsAbs(envPath) || !regexp.MustCompile(`^[a-z][a-z0-9_-]{0,39}$`).MatchString(project) {
		return nil, errors.New("invalid adoption path or project")
	}
	if _, err := loadConfig(); !os.IsNotExist(err) {
		return nil, Fail("ALREADY_MANAGED", "An installation record already exists or cannot be safely inspected.", "Use doctor and update for an existing managed installation.")
	}
	file, err := os.OpenFile(envPath, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 65536 {
		return nil, errors.New("invalid environment file")
	}
	data, err := io.ReadAll(io.LimitReader(file, 65537))
	if err != nil {
		return nil, err
	}
	values, err := ParseEnv(data)
	if err != nil {
		return nil, err
	}
	values, err = EnsureSecrets(values, true)
	if err != nil {
		return nil, err
	}
	manifest, _, err := e.fetch(ctx, version)
	if err != nil {
		return nil, err
	}
	if Newer(manifest.MinimumInstaller, e.Version) {
		return nil, Fail("INSTALLER_UPDATE_REQUIRED", "The target release requires a newer installer.", "Install the matching signed installer first.")
	}
	host, preflight := Inspect(ctx, e.runner())
	if preflight.Overall == "blocked" {
		return preflight, Fail("PREFLIGHT_BLOCKED", "Host prerequisites are not ready.", "Resolve the reported host checks.")
	}
	images := map[string]string{}
	containers := map[string]string{}
	for _, service := range []string{"postgres", "openlab-server", "openlab-worker", "openlab-web"} {
		raw, err := e.runner().Run(ctx, 10*time.Second, nil, "docker", "ps", "-a", "--filter", "label=com.docker.compose.project="+project, "--filter", "label=com.docker.compose.service="+service, "--format", "{{.ID}}")
		id := strings.TrimSpace(string(raw))
		if err != nil || !regexp.MustCompile(`^[a-f0-9]{12,64}$`).MatchString(id) {
			return nil, Fail("ADOPTION_AMBIGUOUS", "Exactly one existing container per OpenLab service is required.", "Verify the Compose project name and start the original stack.")
		}
		containers[service] = id
		raw, err = e.runner().Run(ctx, 10*time.Second, nil, "docker", "inspect", "--format", "{{.Image}}", id)
		image := strings.TrimSpace(string(raw))
		if err != nil || !regexp.MustCompile(`^sha256:[a-f0-9]{64}$`).MatchString(image) {
			return nil, errors.New("cannot pin the current image identity")
		}
		images[service] = image
	}
	if images["openlab-worker"] != images["openlab-server"] {
		return nil, Fail("ADOPTION_IMAGE_MISMATCH", "Server and worker images differ.", "Align the existing deployment images before adoption.")
	}
	for service, expected := range map[string]string{"postgres": project + "_openlab-postgres", "openlab-server": project + "_openlab-data", "openlab-worker": project + "_openlab-data"} {
		raw, err := e.runner().Run(ctx, 10*time.Second, nil, "docker", "inspect", "--format", "{{json .Mounts}}", containers[service])
		if err != nil {
			return nil, err
		}
		var mounts []struct {
			Type        string
			Name        string
			Destination string
		}
		if json.Unmarshal(raw, &mounts) != nil {
			return nil, errors.New("invalid volume inventory")
		}
		destination := "/var/lib/openlab"
		if service == "postgres" {
			destination = "/var/lib/postgresql/data"
		}
		found := false
		for _, mount := range mounts {
			if mount.Destination == destination && mount.Type == "volume" && mount.Name == expected {
				found = true
			}
		}
		if !found {
			return nil, Fail("ADOPTION_VOLUME_MISMATCH", "Existing data mounts do not match the selected Compose project.", "Use manual migration for custom bind mounts or external volumes; nothing was changed.")
		}
	}
	raw, err := e.runner().Run(ctx, 15*time.Second, nil, "docker", "exec", containers["postgres"], "psql", "-U", values["POSTGRES_USER"], "-d", values["POSTGRES_DB"], "-Atc", "SELECT version_num FROM alembic_version")
	schema := strings.TrimSpace(string(raw))
	if err != nil || !compatibleSchema(manifest, schema) {
		return nil, Fail("ADOPTION_SCHEMA_INCOMPATIBLE", "The signed release does not declare rollback compatibility with the existing schema.", "Review a supported migration release; the old stack was not changed.")
	}
	bundleURL, _ := releaseURL(manifest.Version, "openlab-bundle.tar.gz")
	bundle, err := e.download(ctx, bundleURL, 1024*1024)
	if err != nil {
		return nil, err
	}
	files, err := ExtractBundle(bundle, manifest.BundleSHA256)
	if err != nil {
		return nil, err
	}
	// Root-owned state only from here on. Existing checkout and env remain untouched.
	if err = e.prepareControl(); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(StateRoot+"/operation.lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return nil, Fail("INSTALLER_BUSY", "Another installer operation is active.", "Retry after it completes.")
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	config := Config{SchemaVersion: 1, Version: "v0.0.0", Project: project, BindAddress: host.BindAddress, Port: 3000, Images: Images{Server: images["openlab-server"], Web: images["openlab-web"], Postgres: images["postgres"]}, SchemaRevision: schema}
	// Existing automation is never modified. Adopted installs start with scheduled updates disabled.
	policy, _ := json.Marshal(Policy{Hour: 3})
	if err = AtomicWrite(StateRoot+"/control/policy/policy.json", policy, 0600); err != nil {
		return nil, err
	}
	if err = os.Chown(StateRoot+"/control/policy/policy.json", 10001, 10001); err != nil {
		return nil, err
	}
	for name, content := range files {
		if err = AtomicWrite(AppRoot+"/"+name, content, 0600); err != nil {
			return nil, err
		}
	}
	configureRelease(values, config)
	env, err := EncodeEnv(values)
	if err != nil {
		return nil, err
	}
	if err = AtomicWrite(ConfigRoot+"/openlab.env", env, 0600); err != nil {
		return nil, err
	}
	if err = writeNetwork(config); err != nil {
		return nil, err
	}
	if err = saveConfig(config); err != nil {
		return nil, err
	}
	result, err := e.updateVersion(ctx, true, manifest.Version)
	if err == nil && host.Systemd {
		err = e.installTimers(ctx)
	}
	return result, err
}
