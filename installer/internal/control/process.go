package control

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// Runner is injectable for tests. No operation ever invokes a shell with user input.
type Runner interface {
	Run(context.Context, time.Duration, []byte, string, ...string) ([]byte, error)
}
type StreamRunner interface {
	RunTo(context.Context, time.Duration, io.Writer, string, ...string) error
}
type SystemRunner struct{ Secrets []string }
type cappedBuffer struct {
	bytes.Buffer
	limit int
}

func (b *cappedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if remaining := b.limit - b.Len(); remaining > 0 {
		if len(p) > remaining {
			p = p[:remaining]
		}
		_, _ = b.Buffer.Write(p)
	}
	return n, nil
}

func approvedExecutable(name string) (string, error) {
	allowed := map[string]bool{"docker": true, "systemctl": true, "apt-get": true, "tailscale": true, "visudo": true}
	if !allowed[name] {
		return "", errors.New("executable is not approved")
	}
	var executable string
	for _, directory := range []string{"/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin"} {
		candidate := filepath.Join(directory, name)
		if info, err := os.Stat(candidate); err == nil && info.Mode().IsRegular() && info.Mode().Perm()&0111 != 0 {
			executable = candidate
			break
		}
	}
	if executable == "" {
		return "", Fail("DEPENDENCY_MISSING", name+" is not installed.", "openlabctl install --install-deps")
	}
	return executable, nil
}

func (r SystemRunner) Run(ctx context.Context, timeout time.Duration, input []byte, name string, args ...string) ([]byte, error) {
	executable, err := approvedExecutable(name)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	command := exec.CommandContext(ctx, executable, args...)
	command.Env = []string{"PATH=/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin", "HOME=/root", "LANG=C.UTF-8", "DEBIAN_FRONTEND=noninteractive"}
	command.Stdin = bytes.NewReader(input)
	output := &cappedBuffer{limit: 65536}
	command.Stdout = output
	command.Stderr = output
	err = command.Run()
	cleaned := []byte(Redact(output.String(), r.Secrets))
	if ctx.Err() != nil {
		return cleaned, Fail("OPERATION_TIMEOUT", name+" did not finish within its time limit.", "Inspect service status before retrying.")
	}
	if err != nil {
		return cleaned, Fail("COMMAND_FAILED", name+" reported a failure.", "openlabctl doctor")
	}
	return cleaned, nil
}

// Binary backup bytes are streamed directly to a root-readable file, never logs
// or MCP output. Only the fixed pg_dump recipe calls this path.
func (r SystemRunner) RunTo(ctx context.Context, timeout time.Duration, output io.Writer, name string, args ...string) error {
	if name != "docker" || len(args) < 3 || args[0] != "exec" || args[2] != "pg_dump" {
		return errors.New("unapproved streaming operation")
	}
	executable, err := approvedExecutable(name)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	command := exec.CommandContext(ctx, executable, args...)
	command.Env = []string{"PATH=/usr/bin:/bin", "HOME=/root", "LANG=C.UTF-8"}
	command.Stdout = output
	command.Stderr = &cappedBuffer{limit: 4096}
	if err = command.Run(); err != nil {
		return Fail("BACKUP_FAILED", "The database dump did not complete.", "Inspect database health and disk space.")
	}
	return nil
}

var secretLine = regexp.MustCompile(`(?im)^.*(?:password|secret(?:[_ -]?key)?|encryption[_ -]?key|api[_ -]?key|(?:setup[_ -]?)?token|authorization)\s*[:=].*$`)
var bearer = regexp.MustCompile(`(?i)Bearer\s+[^\s"']+`)
var urlCredential = regexp.MustCompile(`(https?|postgres(?:ql)?(?:\+psycopg)?):\/\/[^\s/@]+:[^\s/@]+@`)

func Redact(output string, secrets []string) string {
	for _, secret := range secrets {
		if len(secret) >= 4 {
			output = strings.ReplaceAll(output, secret, "[redacted]")
		}
	}
	output = secretLine.ReplaceAllString(output, "[redacted credential line]")
	output = bearer.ReplaceAllString(output, "Bearer [redacted]")
	output = urlCredential.ReplaceAllString(output, "$1://[redacted]@")
	// Strip terminal control characters to prevent malicious service output spoofing a terminal.
	output = strings.Map(func(r rune) rune {
		if r < 32 && r != '\n' && r != '\t' {
			return -1
		}
		return r
	}, output)
	return output
}
