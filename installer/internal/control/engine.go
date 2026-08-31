package control

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const AppRoot = "/opt/openlab"
const ConfigRoot = "/etc/openlab"
const StateRoot = "/var/lib/openlab-installer"
const HelperPath = "/usr/local/libexec/openlabctl-helper"
const BinaryPath = "/usr/local/bin/openlabctl"

type Config struct {
	SchemaVersion  int    `json:"schema_version"`
	Version        string `json:"version"`
	Project        string `json:"project"`
	BindAddress    string `json:"bind_address"`
	Port           int    `json:"port"`
	Images         Images `json:"images"`
	SchemaRevision string `json:"schema_revision"`
	SourceMode     bool   `json:"source_mode"`
	TailscaleIP    string `json:"tailscale_ip,omitempty"`
	KicadEnabled   bool   `json:"kicad_enabled,omitempty"`
}

func (c Config) WorkerImage() string {
	if c.KicadEnabled {
		return c.Images.KicadWorker
	}
	return c.Images.Server
}

func (c Config) Validate() error {
	if c.KicadEnabled && c.Images.KicadWorker == "" {
		return errors.New("enabled KiCad requires a signed worker image")
	}
	if c.SchemaVersion != 1 || (!ValidRelease(c.Version) && !c.SourceMode) || !regexp.MustCompile(`^[a-z][a-z0-9_-]{0,39}$`).MatchString(c.Project) {
		return errors.New("invalid installation configuration")
	}
	ip := net.ParseIP(c.BindAddress)
	if ip == nil || ip.To4() == nil || (!ip.IsPrivate() && !ip.IsLoopback()) || c.Port < 1024 || c.Port > 65535 {
		return errors.New("binding must use a private or loopback IPv4 address and an unprivileged port")
	}
	if c.TailscaleIP != "" {
		_, tailnet, _ := net.ParseCIDR("100.64.0.0/10")
		if !tailnet.Contains(net.ParseIP(c.TailscaleIP)) {
			return errors.New("invalid Tailscale address")
		}
	}
	return nil
}

type Plan struct {
	ID          string   `json:"plan_id"`
	Version     string   `json:"version"`
	InstallDeps bool     `json:"install_deps"`
	Port        int      `json:"port"`
	Host        Host     `json:"host"`
	Preflight   Report   `json:"preflight"`
	Actions     []string `json:"actions"`
}

type Engine struct {
	Version   string
	PublicKey string
	Runner    Runner
	// Progress is terminal-only, fixed text emitted by lifecycle operations.
	// It must never include command output, configuration, or credentials.
	Progress func(string)
	// Detail updates the current line; subprocess text is redacted before this callback.
	Detail func(string)
	// Private dependency seams for disposable lifecycle fault-injection tests.
	fetchRelease  func(context.Context, string, string) (Manifest, []byte, error)
	downloadAsset func(context.Context, string, int64) ([]byte, error)
	probeReady    func(context.Context) (Report, error)
}

func (e *Engine) progress(message string) {
	if e.Progress != nil {
		e.Progress(message)
	}
}

func (e *Engine) detail(message string) {
	if e.Detail != nil {
		e.Detail(message)
	}
}

func (e *Engine) fetch(ctx context.Context, version string) (Manifest, []byte, error) {
	if e.fetchRelease != nil {
		return e.fetchRelease(ctx, version, e.PublicKey)
	}
	return FetchManifest(ctx, version, e.PublicKey)
}
func (e *Engine) download(ctx context.Context, url string, limit int64) ([]byte, error) {
	if e.downloadAsset != nil {
		return e.downloadAsset(ctx, url, limit)
	}
	return Download(ctx, url, limit)
}
func (e *Engine) runner() Runner {
	if e.Runner != nil {
		return e.Runner
	}
	values, _ := readEnvironment()
	secrets := []string{}
	for _, key := range []string{"POSTGRES_PASSWORD", "DATABASE_URL", "OPENLAB_SECRET_KEY", "OPENLAB_ENCRYPTION_KEY", "OPENLAB_SETUP_TOKEN"} {
		if values[key] != "" {
			secrets = append(secrets, values[key])
		}
	}
	return SystemRunner{Secrets: secrets, Progress: e.Detail}
}
func trustedPath(path string) error {
	for current := filepath.Clean(path); current != "/"; current = filepath.Dir(current) {
		info, err := os.Lstat(current)
		if err != nil {
			return err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || stat.Uid != 0 || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
			return Fail("UNTRUSTED_INSTALLATION_PATH", "Installation files must be root-owned and not writable by other users.", "Inspect ownership of /etc/openlab and /opt/openlab.")
		}
	}
	return nil
}
func trustedFile(path string) ([]byte, error) {
	if err := trustedPath(path); err != nil {
		return nil, err
	}
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() > 65536 {
		return nil, errors.New("invalid installation file")
	}
	return os.ReadFile(path)
}
func loadConfig() (Config, error) {
	var config Config
	data, err := trustedFile(ConfigRoot + "/installation.json")
	if err != nil {
		return config, err
	}
	if err = DecodeStrict(data, &config); err != nil {
		return config, err
	}
	return config, config.Validate()
}
func saveConfig(config Config) error {
	if err := config.Validate(); err != nil {
		return err
	}
	data, _ := json.MarshalIndent(config, "", "  ")
	return AtomicWrite(ConfigRoot+"/installation.json", data, 0600)
}
func readEnvironment() (map[string]string, error) {
	data, err := trustedFile(ConfigRoot + "/openlab.env")
	if err != nil {
		return nil, err
	}
	return ParseEnv(data)
}

func (e *Engine) compose(ctx context.Context, timeout time.Duration, config Config, args ...string) ([]byte, error) {
	// Never use COMPOSE_FILE, DOCKER_HOST, or the caller's Docker context/environment.
	for _, path := range []string{AppRoot + "/deploy/compose.yml", AppRoot + "/deploy/compose.installer.yml", ConfigRoot + "/network.yml", ConfigRoot + "/openlab.env"} {
		if _, err := trustedFile(path); err != nil {
			return nil, err
		}
	}
	base := []string{"compose", "--project-name", config.Project, "--env-file", ConfigRoot + "/openlab.env", "-f", AppRoot + "/deploy/compose.yml", "-f", AppRoot + "/deploy/compose.installer.yml", "-f", ConfigRoot + "/network.yml"}
	return e.runner().Run(ctx, timeout, nil, "docker", append(base, args...)...)
}

func (e *Engine) composeProgress(ctx context.Context, timeout time.Duration, config Config, args ...string) ([]byte, error) {
	// Parse plain events into our own display: Docker's ANSI stream cannot be
	// forwarded safely through redaction or the helper's JSON protocol.
	for _, path := range []string{AppRoot + "/deploy/compose.yml", AppRoot + "/deploy/compose.installer.yml", ConfigRoot + "/network.yml", ConfigRoot + "/openlab.env"} {
		if _, err := trustedFile(path); err != nil {
			return nil, err
		}
	}
	base := []string{"compose", "--progress", "plain", "--project-name", config.Project, "--env-file", ConfigRoot + "/openlab.env", "-f", AppRoot + "/deploy/compose.yml", "-f", AppRoot + "/deploy/compose.installer.yml", "-f", ConfigRoot + "/network.yml"}
	runner := e.runner()
	if progress, ok := runner.(ProgressRunner); ok {
		return progress.RunProgress(ctx, timeout, nil, "docker", append(base, args...)...)
	}
	return runner.Run(ctx, timeout, nil, "docker", append(base, args...)...)
}

func (e *Engine) Plan(ctx context.Context, request Request) (Plan, Manifest, error) {
	manifest, raw, err := e.fetch(ctx, request.Version)
	if err != nil {
		return Plan{}, manifest, err
	}
	if Newer(manifest.MinimumInstaller, e.Version) {
		return Plan{}, manifest, Fail("INSTALLER_UPDATE_REQUIRED", "This release needs a newer installer.", "Upgrade openlabctl using the verified bootstrap.")
	}
	host, report := Inspect(ctx, e.runner())
	bind := host.BindAddress
	port := 3000
	existingVersion := ""
	project := "openlab"
	if config, err := loadConfig(); err == nil {
		bind = config.BindAddress
		port = config.Port
		existingVersion = config.Version
		project = config.Project
	} else if !os.IsNotExist(err) {
		return Plan{}, manifest, err
	}
	if request.BindAddress != "" {
		if existingVersion != "" && (request.BindAddress != bind || request.Port != port) {
			return Plan{}, manifest, Fail("NETWORK_CHANGE_REQUIRED", "Existing installation binding differs from this request.", "Use openlabctl network bind explicitly.")
		}
		bind = request.BindAddress
		port = request.Port
	}
	host.BindAddress = bind
	if existingVersion == "" {
		listener, err := net.Listen("tcp", net.JoinHostPort(bind, strconv.Itoa(port)))
		if err == nil {
			listener.Close()
		}
		report.Checks = append(report.Checks, NewCheck("port", "Web port", true, err == nil, "PORT_IN_USE", "The selected web port must be available before installation.", "Choose a private address and free port with install --bind ADDRESS --port PORT."))
	}
	if request.InstallDeps && (host.Distribution == "debian" || host.Distribution == "ubuntu" || host.Distribution == "raspbian") {
		for index := range report.Checks {
			if report.Checks[index].ID == "docker" || report.Checks[index].ID == "compose" {
				if report.Checks[index].Status != "pass" {
					report.Checks[index].Required = false
					report.Checks[index].Status = "warn"
					report.Checks[index].Summary = "Will install the prerequisite because install_deps was explicitly enabled."
				}
			}
		}
	}
	report = Summarize(report.Checks, e.Version)
	digest := sha256.Sum256(bytes.Join([][]byte{raw, []byte(host.Architecture), []byte(bind), []byte(strconv.Itoa(port)), []byte(project), []byte(existingVersion), []byte(strconv.FormatBool(request.InstallDeps))}, []byte{0}))
	plan := Plan{ID: hex.EncodeToString(digest[:]), Version: manifest.Version, InstallDeps: request.InstallDeps, Port: port, Host: host, Preflight: report,
		Actions: []string{"Verify release signature and immutable image digests", "Preserve existing environment and named data volumes", "Pull release images and apply database migrations", "Start services and validate readiness", "Display the browser setup address"}}
	return plan, manifest, nil
}

func (e *Engine) Handle(ctx context.Context, request Request) (any, error) {
	if err := request.Validate(); err != nil {
		return nil, Fail("INVALID_REQUEST", "The requested installer action or parameters are invalid.", "Use a documented command or tool schema.")
	}
	if request.Action == "inspect" {
		host, report := Inspect(ctx, e.runner())
		return map[string]any{"host": host, "report": report}, nil
	}
	if os.Geteuid() != 0 {
		return nil, Fail("AUTHORIZATION_REQUIRED", "The one-time scoped OpenLab grant has not been applied.", "Run openlabctl authorize once from your terminal.")
	}
	switch request.Action {
	case "plan":
		plan, _, err := e.Plan(ctx, request)
		return plan, err
	case "status":
		return e.Doctor(ctx, false)
	case "logs":
		config, err := loadConfig()
		if err != nil {
			return nil, err
		}
		lines := request.Lines
		if lines == 0 {
			lines = 100
		}
		output, err := e.compose(ctx, 15*time.Second, config, "logs", "--no-color", "--tail", strconv.Itoa(lines), request.Service)
		return map[string]string{"logs": string(output)}, err
	case "check-updates":
		return e.CheckUpdate(ctx)
	}
	// One host mutation at a time, across CLI invocations and MCP clients.
	if err := os.MkdirAll(StateRoot, 0700); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(StateRoot+"/operation.lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return nil, Fail("INSTALLER_BUSY", "Another OpenLab operation is in progress.", "Inspect status and retry after it finishes.")
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	switch request.Action {
	case "install":
		return e.Install(ctx, request)
	case "restart":
		config, err := loadConfig()
		if err != nil {
			return nil, err
		}
		if _, err = e.compose(ctx, 2*time.Minute, config, "restart"); err != nil {
			return nil, err
		}
		return e.waitReady(ctx)
	case "repair":
		return e.Repair(ctx, request.Repair)
	case "backup":
		return e.Backup(ctx)
	case "update":
		version := request.Version
		if version == "" {
			version = "latest"
		}
		return e.updateVersion(ctx, request.ManualFeature, version)
	case "bind":
		return e.Bind(ctx, request.BindAddress, request.Port)
	case "tailscale":
		return e.Tailscale(ctx, request.InstallDeps)
	}
	return nil, errors.New("unsupported operation")
}

func (e *Engine) Doctor(ctx context.Context, publish bool) (Report, error) {
	config, err := loadConfig()
	if err != nil {
		return Report{}, Fail("NOT_INSTALLED", "No valid managed installation was found.", "Run openlabctl install or adopt an existing installation.")
	}
	host, preflight := Inspect(ctx, e.runner())
	checks := preflight.Checks
	// Installed services need operating headroom, not the larger initial pull budget.
	for index := range checks {
		if checks[index].ID == "disk" {
			checks[index] = NewCheck("disk", "Operating disk space", true, host.FreeBytes >= 512*1024*1024, "DISK_LOW", "At least 512 MiB of operating headroom is required.", "Free disk space without removing OpenLab volumes.")
		}
	}
	containerIDs := map[string]string{}
	for _, service := range []string{"postgres", "openlab-server", "openlab-worker", "openlab-web"} {
		raw, err := e.compose(ctx, 15*time.Second, config, "ps", "--all", "--quiet", service)
		id := strings.TrimSpace(string(raw))
		running := false
		imageMatches := false
		if err == nil && regexp.MustCompile(`^[a-f0-9]{12,64}$`).MatchString(id) {
			containerIDs[service] = id
			state, stateErr := e.runner().Run(ctx, 10*time.Second, nil, "docker", "inspect", "--format", "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}", id)
			running = stateErr == nil && strings.HasPrefix(string(state), "running")
			if service == "postgres" || service == "openlab-server" {
				running = running && strings.Contains(string(state), "healthy") && !strings.Contains(string(state), "unhealthy")
			}
			expected := config.Images.Server
			if service == "openlab-worker" {
				expected = config.WorkerImage()
			}
			if service == "postgres" {
				expected = config.Images.Postgres
			}
			if service == "openlab-web" {
				expected = config.Images.Web
			}
			actual, actualErr := e.runner().Run(ctx, 10*time.Second, nil, "docker", "inspect", "--format", "{{.Image}}", id)
			wanted, wantedErr := e.runner().Run(ctx, 10*time.Second, nil, "docker", "image", "inspect", "--format", "{{.Id}}", expected)
			imageMatches = actualErr == nil && wantedErr == nil && bytes.Equal(bytes.TrimSpace(actual), bytes.TrimSpace(wanted))
		}
		checks = append(checks, NewCheck(strings.ReplaceAll(service, "-", "_"), service, true, running && imageMatches, "SERVICE_NOT_READY", "Service state and image identity must match the installed release.", "openlabctl logs --service "+service))
		e.detail(readinessProgress(checks))
	}
	endpoint := "http://" + net.JoinHostPort(config.BindAddress, strconv.Itoa(config.Port))
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	for _, route := range []struct {
		id, path string
		status   int
	}{{"api", "/api/v1/health", 200}, {"web", "/login", 200}, {"setup", "/api/v1/setup", 200}, {"page_guard", "/settings", 307}} {
		request, _ := http.NewRequestWithContext(ctx, "GET", endpoint+route.path, nil)
		response, err := client.Do(request)
		ok := err == nil && response.StatusCode == route.status
		if err == nil {
			if route.id == "page_guard" {
				location, parseErr := response.Location()
				ok = ok && parseErr == nil && location.Path == "/login"
			}
			response.Body.Close()
		}
		checks = append(checks, NewCheck(route.id, "HTTP "+route.path, true, ok, "HTTP_NOT_READY", "The application route must answer with the expected status.", "openlabctl logs --service openlab-web"))
		e.detail(readinessProgress(checks))
	}
	heartbeatCode := "import json; from datetime import UTC,datetime; from sqlalchemy import select; from openlab.db import SessionLocal; from openlab.models import ServiceHeartbeat; from openlab.config import get_settings; db=SessionLocal(); row=db.scalar(select(ServiceHeartbeat).where(ServiceHeartbeat.service=='worker').order_by(ServiceHeartbeat.last_seen_at.desc()).limit(1)); print(json.dumps({'ready':bool(row and row.version==get_settings().version and 0 <= (datetime.now(UTC)-row.last_seen_at).total_seconds() <= 60)})); db.close()"
	heartbeat := []byte(nil)
	heartbeatErr := errors.New("OpenLab server container is unavailable")
	if serverID := containerIDs["openlab-server"]; serverID != "" {
		// Compose CLI startup alone exceeded the former 15-second budget on a
		// Raspberry Pi 3B. The validated server container ID is already known,
		// so a direct Docker exec remains bounded and avoids that extra overhead.
		heartbeat, heartbeatErr = e.runner().Run(ctx, 30*time.Second, nil, "docker", "exec", serverID, "python", "-c", heartbeatCode)
	}
	var heartbeatResult struct {
		Ready bool `json:"ready"`
	}
	healthyWorker := heartbeatErr == nil && json.Unmarshal(heartbeat, &heartbeatResult) == nil && heartbeatResult.Ready
	checks = append(checks, NewCheck("worker_heartbeat", "Worker heartbeat", true, healthyWorker, "WORKER_UNAVAILABLE", "A matching worker must report within 60 seconds.", "openlabctl repair worker"))
	e.detail(readinessProgress(checks))
	report := Summarize(checks, config.Version)
	if publish {
		if err = e.publishStatus(report, e.tailscaleState(ctx), ""); err != nil {
			return report, err
		}
	}
	return report, nil
}

func (e *Engine) publishStatus(report Report, tailscale, update string) error {
	previous := Status{Tailscale: "unavailable", UpdateStatus: "idle"}
	if data, err := os.ReadFile(StateRoot + "/control/status.json"); err == nil {
		_ = DecodeStrict(data, &previous)
	}
	if tailscale == "" {
		tailscale = previous.Tailscale
	}
	if update == "" {
		update = previous.UpdateStatus
	}
	value := Status{SchemaVersion: 1, CheckedAt: time.Now().UTC(), Version: report.Version, Checks: report.Checks, Tailscale: tailscale, UpdateStatus: update}
	data, _ := json.MarshalIndent(value, "", "  ")
	return AtomicWrite(StateRoot+"/control/status.json", data, 0644)
}

func (e *Engine) waitReady(ctx context.Context) (Report, error) {
	if e.probeReady != nil {
		return e.probeReady(ctx)
	}
	return e.waitForReadiness(ctx, 5*time.Minute, func(ctx context.Context) (Report, error) { return e.Doctor(ctx, true) })
}

func (e *Engine) waitForReadiness(ctx context.Context, readinessWindow time.Duration, probe func(context.Context) (Report, error)) (Report, error) {
	ctx, cancel := context.WithTimeout(ctx, readinessWindow)
	defer cancel()
	e.progress("Readiness")
	e.detail(readinessProgress(nil))
	var report Report
	for ctx.Err() == nil {
		next, err := probe(ctx)
		report = next
		if err == nil && ctx.Err() == nil && (report.Overall == "ready" || report.Overall == "ready_with_warnings") {
			e.progress("Services ready")
			return report, nil
		}
		e.detail(readinessProgress(report.Checks))
		select {
		case <-ctx.Done():
		case <-time.After(3 * time.Second):
		}
	}
	if ctx.Err() == context.Canceled {
		return report, ctx.Err()
	}
	e.progress("Readiness timed out; run openlabctl doctor")
	return report, Fail("READINESS_TIMEOUT", "OpenLab did not become ready within five minutes.", "openlabctl doctor")
}

func writeNetwork(config Config) error {
	if err := config.Validate(); err != nil {
		return err
	}
	ports := fmt.Sprintf("      - \"127.0.0.1:%d:3000\"\n", config.Port)
	if config.BindAddress != "127.0.0.1" {
		ports += fmt.Sprintf("      - \"%s:%d:3000\"\n", config.BindAddress, config.Port)
	}
	if config.TailscaleIP != "" {
		ports += fmt.Sprintf("      - \"%s:%d:3000\"\n", config.TailscaleIP, config.Port)
	}
	return AtomicWrite(ConfigRoot+"/network.yml", []byte("services:\n  openlab-web:\n    ports: !override\n"+ports), 0600)
}
