package control

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

func (e *Engine) Install(ctx context.Context, request Request) (any, error) {
	e.progress("Validating the signed release and host prerequisites.")
	plan, manifest, err := e.Plan(ctx, request)
	if err != nil {
		return nil, err
	}
	if plan.ID != request.PlanID {
		return nil, Fail("PLAN_CHANGED", "The release, host, or installation changed since planning; nothing was applied.", "Generate a fresh installation plan.")
	}
	if plan.Preflight.Overall == "blocked" {
		return plan, Fail("PREFLIGHT_BLOCKED", "Required host checks failed.", "Fix the checks reported by plan_install or openlabctl plan.")
	}
	config, configErr := loadConfig()
	if configErr == nil && config.Version != manifest.Version {
		return nil, Fail("UPDATE_REQUIRED", "An existing installation is on a different release.", "Use openlabctl update so backup and rollback checks are performed.")
	}
	if configErr != nil && !os.IsNotExist(configErr) {
		return nil, configErr
	}
	if !plan.Host.DockerReady || !plan.Host.ComposeReady {
		if !request.InstallDeps {
			return nil, Fail("DEPENDENCY_PERMISSION_REQUIRED", "Docker prerequisites require explicit permission.", "Use --install-deps.")
		}
		if err = e.installDocker(ctx, plan.Host); err != nil {
			return nil, err
		}
	}
	if configErr != nil {
		volumes, err := e.runner().Run(ctx, 15*time.Second, nil, "docker", "volume", "ls", "--filter", "label=com.docker.compose.volume=openlab-postgres", "--format", "{{.Name}}")
		if err != nil {
			return nil, err
		}
		if strings.TrimSpace(string(volumes)) != "" {
			return nil, Fail("ADOPTION_REQUIRED", "Existing OpenLab database volumes were found; a new lab will not be created over them.", "Use openlabctl adopt with the original environment and project.")
		}
		config = Config{SchemaVersion: 1, Version: manifest.Version, Project: "openlab", BindAddress: plan.Host.BindAddress, Port: plan.Port, Images: manifest.Images, SchemaRevision: manifest.SchemaRevision}
	}
	e.progress("Preparing protected OpenLab configuration and local secrets.")
	bundleURL, _ := releaseURL(manifest.Version, "openlab-bundle.tar.gz")
	bundle, err := e.download(ctx, bundleURL, 1024*1024)
	if err != nil {
		return nil, err
	}
	files, err := ExtractBundle(bundle, manifest.BundleSHA256)
	if err != nil {
		return nil, err
	}
	values, err := readEnvironment()
	if os.IsNotExist(err) {
		values = map[string]string{}
	} else if err != nil {
		return nil, err
	}
	_, volumeErr := e.runner().Run(ctx, 10*time.Second, nil, "docker", "volume", "inspect", config.Project+"_openlab-postgres")
	values, err = EnsureSecrets(values, volumeErr == nil)
	if err != nil {
		return nil, err
	}
	configureRelease(values, config)
	if err = e.prepareControl(); err != nil {
		return nil, err
	}
	for name, content := range files {
		if err = AtomicWrite(AppRoot+"/"+name, content, 0600); err != nil {
			return nil, err
		}
	}
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
	e.progress("Pulling verified container images. This can take several minutes on a Raspberry Pi.")
	if _, err = e.composeProgress(ctx, 10*time.Minute, config, "pull"); err != nil {
		return nil, err
	}
	e.progress("Starting OpenLab services and applying the release database schema.")
	if _, err = e.composeProgress(ctx, 5*time.Minute, config, "up", "-d", "--no-build"); err != nil {
		return nil, err
	}
	e.progress("Waiting for the server, web app, database, and worker to become ready.")
	report, err := e.waitReady(ctx)
	if err != nil {
		return report, err
	}
	if plan.Host.Systemd {
		if err = e.installTimers(ctx); err != nil {
			return report, err
		}
	}
	return map[string]any{"phase": "browser_setup", "report": report, "setup_url": fmt.Sprintf("http://%s:%d/setup", config.BindAddress, config.Port), "next": "Open the setup link printed locally by openlabctl setup-link. Owner credentials are entered in the browser, not in AI chat."}, nil
}

func configureRelease(values map[string]string, config Config) {
	values["OPENLAB_SERVER_IMAGE"] = config.Images.Server
	values["OPENLAB_WORKER_IMAGE"] = config.Images.Server
	values["OPENLAB_WEB_IMAGE"] = config.Images.Web
	values["OPENLAB_POSTGRES_IMAGE"] = config.Images.Postgres
	values["OPENLAB_VERSION"] = config.Version
	values["OPENLAB_BIND_ADDRESS"] = config.BindAddress
	values["OPENLAB_PORT"] = fmt.Sprint(config.Port)
	values["OPENLAB_INSTALLER_CONTROL_DIR"] = "/run/openlab-installer"
}

func (e *Engine) prepareControl() error {
	for _, directory := range []string{ConfigRoot, StateRoot, AppRoot, AppRoot + "/deploy", StateRoot + "/control", StateRoot + "/control/policy"} {
		if info, err := os.Lstat(directory); err == nil && info.Mode()&os.ModeSymlink != 0 {
			return errors.New("symlink control directory refused")
		}
		if err := os.MkdirAll(directory, 0700); err != nil {
			return err
		}
	}
	if err := os.Chmod(StateRoot+"/control", 0755); err != nil {
		return err
	}
	if err := os.Chown(StateRoot+"/control/policy", 10001, 10001); err != nil {
		return err
	}
	if err := os.Chmod(StateRoot+"/control/policy", 0750); err != nil {
		return err
	}
	if _, err := os.Lstat(StateRoot + "/control/policy/policy.json"); os.IsNotExist(err) {
		data, _ := json.Marshal(DefaultPolicy())
		if err = AtomicWrite(StateRoot+"/control/policy/policy.json", data, 0600); err != nil {
			return err
		}
		return os.Chown(StateRoot+"/control/policy/policy.json", 10001, 10001)
	}
	return nil
}

func vendorDownload(ctx context.Context, target string) ([]byte, error) {
	parsed, err := url.Parse(target)
	if err != nil || parsed.Scheme != "https" || parsed.User != nil || (parsed.Host != "download.docker.com" && parsed.Host != "pkgs.tailscale.com") {
		return nil, errors.New("vendor URL refused")
	}
	client := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return errors.New("vendor redirect refused") }}
	request, _ := http.NewRequestWithContext(ctx, "GET", target, nil)
	response, err := client.Do(request)
	if err != nil {
		return nil, Fail("VENDOR_UNREACHABLE", "The signed package repository could not be reached.", "Check network access and retry.")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, errors.New("vendor package metadata unavailable")
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil || len(data) > 65536 {
		return nil, errors.New("vendor metadata invalid")
	}
	return data, nil
}

func distroName(host Host) (string, error) {
	distro := host.Distribution
	if distro == "raspbian" {
		distro = "debian"
	}
	if (distro != "debian" && distro != "ubuntu") || !regexp.MustCompile(`^[a-z]{3,20}$`).MatchString(host.Codename) {
		return "", Fail("MANUAL_DEPENDENCIES_REQUIRED", "Automatic package setup supports Debian, Ubuntu and 64-bit Raspberry Pi OS only.", "Install Docker Engine and Compose v2 using your distribution documentation.")
	}
	return distro, nil
}

func (e *Engine) installDocker(ctx context.Context, host Host) error {
	distro, err := distroName(host)
	if err != nil {
		return err
	}
	key, err := vendorDownload(ctx, "https://download.docker.com/linux/"+distro+"/gpg")
	if err != nil {
		return err
	}
	if err = AtomicWrite("/etc/apt/keyrings/openlab-docker.asc", key, 0644); err != nil {
		return err
	}
	if err = os.Chmod("/etc/apt/keyrings", 0755); err != nil {
		return err
	}
	source := fmt.Sprintf("Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/openlab-docker.asc\n", distro, host.Codename, host.Architecture)
	if err = AtomicWrite("/etc/apt/sources.list.d/openlab-docker.sources", []byte(source), 0644); err != nil {
		return err
	}
	if _, err = e.runner().Run(ctx, 5*time.Minute, nil, "apt-get", "update"); err != nil {
		return err
	}
	if _, err = e.runner().Run(ctx, 10*time.Minute, nil, "apt-get", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"); err != nil {
		return err
	}
	_, err = e.runner().Run(ctx, 60*time.Second, nil, "systemctl", "enable", "--now", "docker")
	return err
}

func (e *Engine) Repair(ctx context.Context, recipe string) (any, error) {
	config, err := loadConfig()
	if err != nil {
		return nil, err
	}
	switch recipe {
	case "worker":
		_, err = e.compose(ctx, 60*time.Second, config, "restart", "openlab-worker")
	case "migrations":
		if _, err = e.compose(ctx, 60*time.Second, config, "stop", "openlab-worker", "openlab-server", "openlab-web"); err == nil {
			_, err = e.compose(ctx, 5*time.Minute, config, "run", "--rm", "--no-deps", "openlab-server", "alembic", "upgrade", "head")
			_, startErr := e.compose(ctx, 2*time.Minute, config, "up", "-d", "--no-build")
			if err == nil {
				err = startErr
			}
		}
	case "secrets":
		values, readErr := readEnvironment()
		if readErr != nil {
			return nil, readErr
		}
		values, err = EnsureSecrets(values, true)
		if err == nil {
			data, encodeErr := EncodeEnv(values)
			err = encodeErr
			if err == nil {
				err = AtomicWrite(ConfigRoot+"/openlab.env", data, 0600)
			}
		}
	default:
		return nil, errors.New("unknown repair")
	}
	if err != nil {
		return nil, err
	}
	return e.waitReady(ctx)
}

func (e *Engine) Backup(ctx context.Context) (any, error) {
	config, err := loadConfig()
	if err != nil {
		return nil, err
	}
	values, err := readEnvironment()
	if err != nil {
		return nil, err
	}
	id := time.Now().UTC().Format("20060102T150405.000000000Z")
	directory := StateRoot + "/backups/" + id
	if err = os.MkdirAll(directory, 0700); err != nil {
		return nil, err
	}
	// Recover service availability even if the backup itself fails. No database is restored automatically.
	defer func() { _, _ = e.compose(context.WithoutCancel(ctx), 2*time.Minute, config, "up", "-d", "--no-build") }()
	if _, err = e.compose(ctx, 2*time.Minute, config, "stop", "openlab-worker", "openlab-server", "openlab-web"); err != nil {
		return nil, err
	}
	container, err := e.compose(ctx, 15*time.Second, config, "ps", "--quiet", "postgres")
	if err != nil {
		return nil, err
	}
	containerID := strings.TrimSpace(string(container))
	if !regexp.MustCompile(`^[a-f0-9]{12,64}$`).MatchString(containerID) {
		return nil, errors.New("database container unavailable")
	}
	file, err := os.OpenFile(directory+"/database.dump", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	dumper, ok := e.runner().(StreamRunner)
	if !ok {
		file.Close()
		return nil, errors.New("runner does not support database backup")
	}
	err = dumper.RunTo(ctx, 10*time.Minute, file, "docker", "exec", containerID, "pg_dump", "-U", values["POSTGRES_USER"], "-d", values["POSTGRES_DB"], "-Fc")
	syncErr := file.Sync()
	closeErr := file.Close()
	if err != nil || syncErr != nil || closeErr != nil {
		return nil, Fail("BACKUP_FAILED", "Database backup did not complete; services were restarted.", "Inspect database health and free disk space.")
	}
	if _, err = e.compose(ctx, 5*time.Minute, config, "cp", "openlab-server:/var/lib/openlab", directory+"/attachments"); err != nil {
		return nil, err
	}
	env, err := EncodeEnv(values)
	if err != nil {
		return nil, err
	}
	if err = AtomicWrite(directory+"/openlab.env", env, 0600); err != nil {
		return nil, err
	}
	configData, _ := json.Marshal(config)
	if err = AtomicWrite(directory+"/installation.json", configData, 0600); err != nil {
		return nil, err
	}
	receipt, _ := json.Marshal(map[string]string{"id": id, "version": config.Version, "status": "complete"})
	if err = AtomicWrite(directory+"/receipt.json", receipt, 0600); err != nil {
		return nil, err
	}
	return map[string]string{"backup_id": id, "status": "complete", "note": "Backup contains secrets and stays root-readable in the fixed backup directory."}, nil
}

type UpdateCheck struct {
	Available bool   `json:"available"`
	Eligible  bool   `json:"eligible"`
	Version   string `json:"version"`
	Reason    string `json:"reason"`
}

func compatibleSchema(manifest Manifest, schema string) bool {
	for _, compatible := range manifest.RollbackCompatibleSchemas {
		if compatible == schema {
			return true
		}
	}
	return false
}
func (e *Engine) CheckUpdate(ctx context.Context) (UpdateCheck, error) {
	config, err := loadConfig()
	if err != nil {
		return UpdateCheck{}, err
	}
	manifest, _, err := e.fetch(ctx, "latest")
	if err != nil {
		return UpdateCheck{}, err
	}
	available := Newer(manifest.Version, config.Version)
	eligible := available && !config.SourceMode && manifest.Classification == "security" && manifest.UnattendedSafe && compatibleSchema(manifest, config.SchemaRevision) && !Newer(manifest.MinimumInstaller, e.Version)
	reason := "No newer release."
	if available {
		reason = "Manual review is required for this release."
	}
	if eligible {
		reason = "Signed security release with backward-compatible migrations."
	}
	return UpdateCheck{Available: available, Eligible: eligible, Version: manifest.Version, Reason: reason}, nil
}

func (e *Engine) Update(ctx context.Context, manualFeature bool) (any, error) {
	return e.updateVersion(ctx, manualFeature, "latest")
}
func (e *Engine) updateVersion(ctx context.Context, manualFeature bool, version string) (any, error) {
	config, err := loadConfig()
	if err != nil {
		return nil, err
	}
	manifest, _, err := e.fetch(ctx, version)
	if err != nil {
		return nil, err
	}
	if !Newer(manifest.Version, config.Version) {
		return map[string]string{"status": "current", "version": config.Version}, nil
	}
	if config.SourceMode || (!manualFeature && (manifest.Classification != "security" || !manifest.UnattendedSafe)) || !compatibleSchema(manifest, config.SchemaRevision) || Newer(manifest.MinimumInstaller, e.Version) {
		return nil, Fail("MANUAL_UPDATE_REQUIRED", "This release cannot be safely applied by the updater.", "Review release compatibility and upgrade instructions; use --feature for a compatible feature release.")
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
	previous := map[string][]byte{}
	for _, path := range []string{ConfigRoot + "/installation.json", ConfigRoot + "/openlab.env", AppRoot + "/deploy/compose.yml", AppRoot + "/deploy/compose.installer.yml"} {
		data, readErr := trustedFile(path)
		if readErr != nil {
			return nil, readErr
		}
		previous[path] = data
	}
	backup, err := e.Backup(ctx)
	if err != nil {
		return nil, err
	}
	next := config
	next.Version = manifest.Version
	next.Images = manifest.Images
	next.SchemaRevision = manifest.SchemaRevision
	values, err := readEnvironment()
	if err != nil {
		return nil, err
	}
	configureRelease(values, next)
	env, err := EncodeEnv(values)
	if err != nil {
		return nil, err
	}
	apply := func() error {
		for name, data := range files {
			if err := AtomicWrite(AppRoot+"/"+name, data, 0600); err != nil {
				return err
			}
		}
		if err := AtomicWrite(ConfigRoot+"/openlab.env", env, 0600); err != nil {
			return err
		}
		if err := saveConfig(next); err != nil {
			return err
		}
		e.progress("Pulling the verified update images. Docker progress will appear below.")
		if _, err := e.composeProgress(ctx, 10*time.Minute, next, "pull"); err != nil {
			return err
		}
		e.progress("Starting updated OpenLab services and applying the release database schema.")
		if _, err := e.composeProgress(ctx, 5*time.Minute, next, "up", "-d", "--no-build", "--force-recreate"); err != nil {
			return err
		}
		report, err := e.waitReady(ctx)
		if err != nil {
			return err
		}
		return e.publishStatus(report, "", "updated")
	}
	if err = apply(); err != nil {
		rollbackCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 8*time.Minute)
		defer cancel()
		for path, data := range previous {
			if restoreErr := AtomicWrite(path, data, 0600); restoreErr != nil {
				return nil, Fail("ROLLBACK_FAILED", "Previous configuration could not be restored; data volumes were preserved.", "Inspect the installation locally.")
			}
		}
		if _, restoreErr := e.compose(rollbackCtx, 2*time.Minute, config, "up", "-d", "--no-build", "--force-recreate"); restoreErr != nil {
			return nil, Fail("ROLLBACK_FAILED", "Previous images could not be restarted; data volumes were preserved.", "Inspect Docker and the saved backup.")
		}
		report, restoreErr := e.waitReady(rollbackCtx)
		_ = e.publishStatus(report, "", "rolled_back")
		if restoreErr != nil {
			return report, Fail("ROLLBACK_UNHEALTHY", "Previous images were restored but readiness still fails.", "openlabctl doctor")
		}
		return report, Fail("UPDATE_ROLLED_BACK", "Update failed readiness; the previous images and configuration were restored. Database migrations were not reversed.", "Inspect the release and diagnostic report before retrying.")
	}
	return map[string]any{"status": "updated", "version": manifest.Version, "backup": backup}, nil
}

func (e *Engine) Tailscale(ctx context.Context, installDeps bool) (any, error) {
	config, err := loadConfig()
	if err != nil {
		return nil, err
	}
	raw, statusErr := e.runner().Run(ctx, 10*time.Second, nil, "tailscale", "status", "--json")
	if statusErr != nil && len(raw) == 0 {
		if !installDeps {
			return nil, Fail("TAILSCALE_NOT_INSTALLED", "Tailscale is unavailable and installation was not authorized.", "Run openlabctl network tailscale --install-deps.")
		}
		host, _ := Inspect(ctx, e.runner())
		distro, err := distroName(host)
		if err != nil {
			return nil, err
		}
		key, err := vendorDownload(ctx, "https://pkgs.tailscale.com/stable/"+distro+"/"+host.Codename+".noarmor.gpg")
		if err != nil {
			return nil, err
		}
		if err = AtomicWrite("/usr/share/keyrings/openlab-tailscale.gpg", key, 0644); err != nil {
			return nil, err
		}
		source := fmt.Sprintf("deb [signed-by=/usr/share/keyrings/openlab-tailscale.gpg] https://pkgs.tailscale.com/stable/%s %s main\n", distro, host.Codename)
		if err = AtomicWrite("/etc/apt/sources.list.d/openlab-tailscale.list", []byte(source), 0644); err != nil {
			return nil, err
		}
		if _, err = e.runner().Run(ctx, 5*time.Minute, nil, "apt-get", "update"); err != nil {
			return nil, err
		}
		if _, err = e.runner().Run(ctx, 5*time.Minute, nil, "apt-get", "install", "-y", "tailscale"); err != nil {
			return nil, err
		}
		if _, err = e.runner().Run(ctx, time.Minute, nil, "systemctl", "enable", "--now", "tailscaled"); err != nil {
			return nil, err
		}
	}
	var status struct {
		BackendState string `json:"BackendState"`
		AuthURL      string `json:"AuthURL"`
		Self         struct {
			TailscaleIPs []string `json:"TailscaleIPs"`
			DNSName      string   `json:"DNSName"`
		} `json:"Self"`
	}
	_ = json.Unmarshal(raw, &status)
	if status.BackendState != "Running" {
		_, _ = e.runner().Run(ctx, 20*time.Second, nil, "tailscale", "up", "--timeout=10s")
		raw, _ = e.runner().Run(ctx, 10*time.Second, nil, "tailscale", "status", "--json")
		_ = json.Unmarshal(raw, &status)
	}
	report, _ := e.Doctor(ctx, false)
	if status.BackendState != "Running" {
		authorization, err := url.Parse(status.AuthURL)
		if err != nil || authorization.Scheme != "https" || authorization.Host != "login.tailscale.com" || authorization.User != nil {
			return nil, Fail("TAILSCALE_AUTH_REQUIRED", "Complete Tailscale login in a local terminal or browser.", "Run tailscale up on the host.")
		}
		_ = e.publishStatus(report, "needs_authorization", "")
		return map[string]string{"status": "authorization_required", "authorization_url": authorization.String()}, nil
	}
	if len(status.Self.TailscaleIPs) == 0 {
		return nil, errors.New("Tailscale has no address")
	}
	config.TailscaleIP = status.Self.TailscaleIPs[0]
	if err = config.Validate(); err != nil {
		return nil, err
	}
	if err = writeNetwork(config); err != nil {
		return nil, err
	}
	if err = saveConfig(config); err != nil {
		return nil, err
	}
	if _, err = e.compose(ctx, 2*time.Minute, config, "up", "-d", "--no-build", "openlab-web"); err != nil {
		return nil, err
	}
	_ = e.publishStatus(report, "connected", "")
	return map[string]string{"status": "connected", "url": fmt.Sprintf("http://%s:%d", strings.TrimSuffix(status.Self.DNSName, "."), config.Port)}, nil
}

func (e *Engine) installTimers(ctx context.Context) error {
	units := map[string]string{
		"openlab-status.service":          "[Unit]\nDescription=Refresh redacted OpenLab diagnostics\nAfter=docker.service\n[Service]\nType=oneshot\nExecStart=" + BinaryPath + " internal status\n",
		"openlab-status.timer":            "[Unit]\nDescription=Refresh OpenLab diagnostics every five minutes\n[Timer]\nOnBootSec=1min\nOnUnitActiveSec=5min\n[Install]\nWantedBy=timers.target\n",
		"openlab-security-update.service": "[Unit]\nDescription=Apply eligible OpenLab security updates during the chosen window\nAfter=docker.service network-online.target\n[Service]\nType=oneshot\nExecStart=" + BinaryPath + " internal scheduled-update\nTimeoutStartSec=30min\n",
		"openlab-security-update.timer":   "[Unit]\nDescription=Check the local OpenLab maintenance policy\n[Timer]\nOnCalendar=*-*-* *:*:00\nAccuracySec=1s\n[Install]\nWantedBy=timers.target\n",
	}
	for name, unit := range units {
		if err := AtomicWrite("/etc/systemd/system/"+name, []byte(unit), 0644); err != nil {
			return err
		}
	}
	if _, err := e.runner().Run(ctx, 30*time.Second, nil, "systemctl", "daemon-reload"); err != nil {
		return err
	}
	_, err := e.runner().Run(ctx, 30*time.Second, nil, "systemctl", "enable", "--now", "openlab-status.timer", "openlab-security-update.timer")
	return err
}
