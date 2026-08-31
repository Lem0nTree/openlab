package control

// The browser can request only these four operations, never a command, image,
// path, URL, or environment variable. Root resolves every target itself.
import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"syscall"
	"time"
)

type SetupRequest struct {
	ID          string    `json:"id"`
	Action      string    `json:"action"`
	RequestedAt time.Time `json:"requested_at"`
}

func (r SetupRequest) Validate(now time.Time) error {
	if !regexp.MustCompile(`^[a-f0-9]{32}$`).MatchString(r.ID) || r.RequestedAt.After(now.Add(time.Minute)) || now.Sub(r.RequestedAt) > 10*time.Minute {
		return errors.New("invalid or expired setup request")
	}
	switch r.Action {
	case "refresh", "kicad", "tailscale", "https":
		return nil
	}
	return errors.New("unsupported setup action")
}

type SetupOperation struct {
	SetupRequest
	Status  string `json:"status"`
	Message string `json:"message"`
	URL     string `json:"url,omitempty"`
}
type SetupStatus struct {
	CheckedAt      time.Time       `json:"checked_at"`
	Tailscale      string          `json:"tailscale"`
	KicadSupported bool            `json:"kicad_supported"`
	Operation      *SetupOperation `json:"operation,omitempty"`
}

func (e *Engine) tailscaleState(ctx context.Context) string {
	raw, err := e.runner().Run(ctx, 5*time.Second, nil, "tailscale", "status", "--json")
	if SafeError(err).Code == "DEPENDENCY_MISSING" {
		return "not_installed"
	}
	var state struct {
		BackendState string
		Self         *struct{ Online bool }
	}
	if err != nil || json.Unmarshal(raw, &state) != nil {
		return "unavailable"
	}
	switch state.BackendState {
	case "Running":
		if state.Self != nil && state.Self.Online {
			return "connected"
		}
	case "NeedsLogin", "NeedsMachineAuth", "Stopped":
		return "needs_authorization"
	}
	return "unavailable"
}

func readSetupRequest(path string) (SetupRequest, error) {
	var request SetupRequest
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return request, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return request, err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	// Two links are possible briefly during the API's exclusive atomic publish.
	// No file is ever written through this descriptor; only bounded JSON is read.
	if !ok || !info.Mode().IsRegular() || stat.Nlink > 2 || (stat.Uid != 10001 && stat.Uid != 0) || info.Size() > 1024 {
		return request, errors.New("invalid setup request file")
	}
	data, err := io.ReadAll(io.LimitReader(file, 1025))
	if err != nil || len(data) > 1024 {
		return request, errors.New("invalid setup request size")
	}
	if err = DecodeStrict(data, &request); err != nil {
		return request, err
	}
	return request, request.Validate(time.Now())
}

func (e *Engine) ProcessSetup(ctx context.Context) (any, error) {
	if os.Geteuid() != 0 {
		return nil, errors.New("root required")
	}
	config, err := loadConfig()
	if err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(StateRoot+"/operation.lock", os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return nil, err
	}
	defer lock.Close()
	if syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil {
		return nil, nil
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	status := SetupStatus{}
	if raw, err := trustedFile(StateRoot + "/control/setup-status.json"); err == nil {
		_ = DecodeStrict(raw, &status)
	}
	status.Tailscale = e.tailscaleState(ctx)
	status.KicadSupported = config.Images.KicadWorker != "" && !config.SourceMode
	publish := func() error {
		status.CheckedAt = time.Now().UTC()
		data, err := json.Marshal(status)
		if err != nil {
			return err
		}
		return AtomicWrite(StateRoot+"/control/setup-status.json", data, 0644)
	}
	// Claim a single fixed file into a root-only directory before inspecting it.
	claimed := StateRoot + "/setup-request.claimed"
	if err := os.Rename(StateRoot+"/control/policy/setup-request.json", claimed); err != nil {
		if !os.IsNotExist(err) {
			return nil, err
		}
		if status.Operation != nil && status.Operation.Status == "running" {
			status.Operation.Status, status.Operation.Message = "failed", "The installer was interrupted. Check host diagnostics before retrying."
		}
		return status, publish()
	}
	defer os.Remove(claimed)
	request, err := readSetupRequest(claimed)
	if err != nil {
		return nil, err
	}
	if status.Operation != nil && (!request.RequestedAt.After(status.Operation.RequestedAt) || request.ID == status.Operation.ID) {
		return nil, errors.New("replayed setup request refused")
	}
	status.Operation = &SetupOperation{SetupRequest: request, Status: "running", Message: "Starting host setup"}
	if err = publish(); err != nil {
		return nil, err
	}
	previousProgress := e.Progress
	defer func() { e.Progress = previousProgress }()
	e.Progress = func(message string) { status.Operation.Message = progressText(message); _ = publish() }
	var target string
	switch request.Action {
	case "refresh":
		_, err = e.Doctor(ctx, true)
	case "kicad":
		err = e.installKicad(ctx, config)
	case "tailscale":
		var result any
		result, err = e.Tailscale(ctx, true)
		if values, ok := result.(map[string]string); ok && values["status"] == "authorization_required" {
			// Login URLs are credentials: never publish them to the shared status file.
			err = Fail("TAILSCALE_AUTH_REQUIRED", "Tailscale is installed. Authorize this device from a local terminal with sudo tailscale up, then check again.", "")
		}
	case "https":
		target, err = e.enableSetupHTTPS(ctx, config)
	}
	status.Operation.Status, status.Operation.Message = "completed", "Host setup completed"
	status.Operation.URL = target
	if err != nil {
		status.Operation.Status = "failed"
		failure := SafeError(err)
		status.Operation.Message = failure.Code + ": " + failure.Message
	}
	status.Tailscale = e.tailscaleState(ctx)
	return status, publish()
}

func (e *Engine) installKicad(ctx context.Context, config Config) error {
	e.progress("Verifying KiCad worker")
	manifest, _, err := e.fetch(ctx, config.Version)
	if err != nil {
		return err
	}
	if config.SourceMode || manifest.Images.KicadWorker == "" || manifest.Images != config.Images {
		return Fail("KICAD_RELEASE_REQUIRED", "This installed release does not provide a matching signed KiCad worker. Upgrade with the current signed installer first.", "")
	}
	// Pull before touching the running worker. No mutable tag or browser image input.
	e.progress("Downloading KiCad worker")
	if _, err = e.runner().Run(ctx, 10*time.Minute, nil, "docker", "pull", manifest.Images.KicadWorker); err != nil {
		return err
	}
	previous, err := trustedFile(ConfigRoot + "/openlab.env")
	if err != nil {
		return err
	}
	values, err := readEnvironment()
	if err != nil {
		return err
	}
	next := config
	next.KicadEnabled = true
	configureRelease(values, next)
	data, err := EncodeEnv(values)
	if err != nil {
		return err
	}
	if err = AtomicWrite(ConfigRoot+"/openlab.env", data, 0600); err != nil {
		return err
	}
	apply := func() error {
		if err := saveConfig(next); err != nil {
			return err
		}
		e.progress("Starting KiCad worker")
		if _, err := e.compose(ctx, 3*time.Minute, next, "up", "-d", "--no-build", "--no-deps", "openlab-worker"); err != nil {
			return err
		}
		if _, err := e.compose(ctx, 30*time.Second, next, "exec", "-T", "openlab-worker", "kicad-cli", "--version"); err != nil {
			return err
		}
		_, err := e.waitReady(ctx)
		return err
	}
	if err = apply(); err == nil {
		return nil
	}
	e.progress("Restoring previous worker")
	rollback, cancel := context.WithTimeout(context.WithoutCancel(ctx), 3*time.Minute)
	defer cancel()
	if AtomicWrite(ConfigRoot+"/openlab.env", previous, 0600) != nil || saveConfig(config) != nil {
		return Fail("KICAD_ROLLBACK_FAILED", "Could not restore the previous worker configuration. Data was preserved; inspect host diagnostics.", "")
	}
	if _, restoreErr := e.compose(rollback, 2*time.Minute, config, "up", "-d", "--no-build", "--no-deps", "openlab-worker"); restoreErr != nil {
		return Fail("KICAD_ROLLBACK_FAILED", "The previous worker could not restart. Data was preserved; inspect host diagnostics.", "")
	}
	return Fail("KICAD_INSTALL_FAILED", "KiCad checks failed; the previous worker configuration was restored.", "")
}

// Never overwrite existing Serve/Funnel configuration, or enable public Funnel.
func (e *Engine) enableSetupHTTPS(ctx context.Context, config Config) (string, error) {
	e.progress("Checking private HTTPS")
	if e.tailscaleState(ctx) != "connected" {
		return "", Fail("TAILSCALE_REQUIRED", "Connect Tailscale before enabling private HTTPS.", "")
	}
	raw, err := e.runner().Run(ctx, 5*time.Second, nil, "tailscale", "status", "--json")
	var state struct{ Self struct{ DNSName string } }
	if err != nil || json.Unmarshal(raw, &state) != nil {
		return "", errors.New("Tailscale DNS unavailable")
	}
	host := strings.TrimSuffix(state.Self.DNSName, ".")
	if !regexp.MustCompile(`^[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$`).MatchString(host) {
		return "", Fail("TAILSCALE_DNS_REQUIRED", "Enable MagicDNS and HTTPS certificates in Tailscale DNS settings first.", "")
	}
	target := fmt.Sprintf("http://127.0.0.1:%d", config.Port)
	raw, err = e.runner().Run(ctx, 5*time.Second, nil, "tailscale", "serve", "status", "--json")
	if err != nil {
		return "", Fail("TAILSCALE_SERVE_UNAVAILABLE", "Update Tailscale to a version supporting Serve and retry.", "")
	}
	var existing map[string]json.RawMessage
	if json.Unmarshal(raw, &existing) != nil {
		return "", errors.New("invalid Serve status")
	}
	if len(existing) != 0 {
		// Only accept our exact existing private reverse proxy. Anything else needs review.
		var expected struct {
			TCP map[string]struct{ HTTPS bool }
			Web map[string]struct {
				Handlers map[string]struct{ Proxy string }
			}
			AllowFunnel map[string]bool
		}
		if DecodeStrict(raw, &expected) != nil || len(expected.AllowFunnel) != 0 || len(expected.TCP) != 1 || !expected.TCP["443"].HTTPS || len(expected.Web) != 1 || len(expected.Web[host+":443"].Handlers) != 1 || expected.Web[host+":443"].Handlers["/"].Proxy != target {
			return "", Fail("HTTPS_CONFIG_EXISTS", "Existing Tailscale Serve or Funnel settings need review; OpenLab did not replace them.", "")
		}
	} else {
		e.progress("Enabling private HTTPS")
		if _, err = e.runner().Run(ctx, 45*time.Second, nil, "tailscale", "serve", "--bg", "--https=443", target); err != nil {
			return "", Fail("HTTPS_APPROVAL_REQUIRED", "Enable HTTPS certificates in Tailscale DNS settings, then retry. No public access was enabled.", "")
		}
	}
	// Verify a real trusted TLS handshake and app health, not just CLI exit status.
	client := &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	url := "https://" + host
	req, _ := http.NewRequestWithContext(ctx, "GET", url+"/api/v1/health", nil)
	response, err := client.Do(req)
	if err != nil {
		return "", Fail("HTTPS_NOT_READY", "Private HTTPS was configured but its certificate or route is not ready. Check Tailscale DNS settings and retry.", "")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return "", Fail("HTTPS_NOT_READY", "The HTTPS application health check did not pass. Retry after startup.", "")
	}
	return url, nil
}
