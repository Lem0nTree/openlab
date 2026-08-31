package control

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/user"
	"regexp"
	"strconv"
	"syscall"
	"time"
)

// Authorize is a terminal-only, one-time trust decision. The helper accepts no
// command-line arguments and only a closed JSON command set on stdin.
func (e *Engine) Authorize(ctx context.Context) error {
	if os.Geteuid() != 0 {
		return Fail("ROOT_REQUIRED", "The one-time grant requires sudo.", "Run sudo openlabctl authorize.")
	}
	account := os.Getenv("SUDO_USER")
	uid := os.Getenv("SUDO_UID")
	if !regexp.MustCompile(`^[a-z_][a-z0-9_-]{0,31}$`).MatchString(account) || account == "root" {
		return errors.New("authorize must be invoked with sudo by the intended non-root user")
	}
	intended, err := user.Lookup(account)
	if err != nil || intended.Uid != uid || uid == "0" {
		return errors.New("sudo user identity could not be verified")
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	binary, err := os.ReadFile(executable)
	if err != nil {
		return err
	}
	if err = AtomicWrite(BinaryPath, binary, 0755); err != nil {
		return err
	}
	if err = AtomicWrite(HelperPath, binary, 0755); err != nil {
		return err
	}
	if err = os.Chmod("/usr/local/libexec", 0755); err != nil {
		return err
	}
	policy := []byte(fmt.Sprintf("# OpenLab scoped lifecycle helper; no command-line arguments permitted.\n%s ALL=(root) NOPASSWD: %s \"\"\n", account, HelperPath))
	candidate := "/etc/sudoers.d/.openlab-" + uid + ".candidate"
	if err = AtomicWrite(candidate, policy, 0440); err != nil {
		return err
	}
	defer os.Remove(candidate)
	if _, err = e.runner().Run(ctx, 10*time.Second, nil, "visudo", "-cf", candidate); err != nil {
		return err
	}
	return AtomicWrite("/etc/sudoers.d/openlab-"+uid, policy, 0440)
}

func CallHelper(ctx context.Context, request Request) (any, error) {
	if err := request.Validate(); err != nil {
		return nil, err
	}
	if err := trustedPath(HelperPath); err != nil {
		return nil, Fail("AUTHORIZATION_REQUIRED", "The scoped helper is missing or has unsafe ownership.", "Run sudo openlabctl authorize once from your terminal.")
	}
	command := exec.CommandContext(ctx, "/usr/bin/sudo", "-n", HelperPath)
	command.Env = []string{"PATH=/usr/bin:/bin", "LANG=C.UTF-8"}
	if os.Getenv("TERM") == "dumb" {
		command.Env = append(command.Env, "TERM=dumb")
	}
	command.Stdin = bytes.NewReader(request.JSON())
	output := &cappedBuffer{limit: 128 * 1024}
	command.Stdout = output
	// Preserve the actual terminal descriptor so the helper can render progress.
	// Redirected stderr stays plain; stdout remains the strict JSON protocol.
	command.Stderr = os.Stderr
	err := command.Run()
	var response struct {
		Result json.RawMessage `json:"result"`
		Error  *Failure        `json:"error,omitempty"`
	}
	if json.Unmarshal(output.Bytes(), &response) != nil {
		return nil, Fail("HELPER_UNAVAILABLE", "The scoped helper could not complete the request.", "Verify the one-time sudo grant with openlabctl authorize.")
	}
	if response.Error != nil {
		return nil, response.Error
	}
	if err != nil {
		return nil, Fail("HELPER_FAILED", "The helper exited before completing the operation.", "openlabctl doctor")
	}
	var result any
	if err = json.Unmarshal(response.Result, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func (e *Engine) ServeHelper(ctx context.Context, input io.Reader, output io.Writer) error {
	if os.Geteuid() != 0 {
		return errors.New("helper requires the scoped sudo grant")
	}
	data, err := io.ReadAll(io.LimitReader(input, 65537))
	var request Request
	if err == nil {
		err = DecodeStrict(data, &request)
	}
	var result any
	if err == nil {
		result, err = e.Handle(ctx, request)
	}
	response := map[string]any{"result": result}
	if err != nil {
		response["error"] = SafeError(err)
	}
	if encodeErr := json.NewEncoder(output).Encode(response); encodeErr != nil {
		return encodeErr
	}
	return err
}

func (e *Engine) ScheduledUpdate(ctx context.Context) (any, error) {
	// The policy directory is app-writable, so only strict, bounded data is read.
	path := StateRoot + "/control/policy/policy.json"
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, Fail("POLICY_INVALID", "Security update policy is missing or unsafe.", "Save a valid policy in Settings.")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
		return nil, errors.New("invalid policy file")
	}
	data, err := io.ReadAll(io.LimitReader(file, 4097))
	if err != nil || len(data) > 4096 {
		return nil, errors.New("invalid policy size")
	}
	var policy Policy
	if err = DecodeStrict(data, &policy); err != nil {
		return nil, err
	}
	if err = policy.Validate(); err != nil {
		return nil, err
	}
	now := time.Now()
	if !policy.Due(now) {
		return map[string]string{"status": "outside_maintenance_window"}, nil
	}
	date := now.Format("2006-01-02") + "-" + strconv.Itoa(policy.Hour) + "-" + strconv.Itoa(policy.Minute)
	receiptPath := StateRoot + "/last-security-window"
	previous, _ := os.ReadFile(receiptPath)
	if string(previous) == date {
		return map[string]string{"status": "already_checked"}, nil
	}
	result, err := e.Handle(ctx, Request{Action: "update"})
	var failure *Failure
	if errors.As(err, &failure) && failure.Code == "INSTALLER_BUSY" {
		return result, err
	}
	if writeErr := AtomicWrite(receiptPath, []byte(date), 0600); writeErr != nil {
		return result, writeErr
	}
	if err != nil && !(failure != nil && (failure.Code == "UPDATE_ROLLED_BACK" || failure.Code == "ROLLBACK_UNHEALTHY")) {
		report, _ := e.Doctor(context.WithoutCancel(ctx), false)
		_ = e.publishStatus(report, "", "failed")
	}
	return result, err
}

// SetupLink is deliberately not an MCP/helper action: bootstrap credentials stay
// in the user's local terminal and must never be included in AI tool output.
func SetupLink() (string, error) {
	if os.Geteuid() != 0 {
		return "", errors.New("setup-link must be run locally using sudo")
	}
	config, err := loadConfig()
	if err != nil {
		return "", err
	}
	values, err := readEnvironment()
	if err != nil {
		return "", err
	}
	if values["OPENLAB_SETUP_TOKEN"] == "" {
		return "", errors.New("no bootstrap token is configured")
	}
	return fmt.Sprintf("http://%s:%d/setup#token=%s", config.BindAddress, config.Port, values["OPENLAB_SETUP_TOKEN"]), nil
}
