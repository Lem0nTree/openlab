// Package control is the shared, bounded command engine for the CLI and installer MCP.
package control

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"regexp"
	"time"
)

const ProtocolVersion = 1

type Check struct {
	ID          string  `json:"id"`
	Label       string  `json:"label"`
	Required    bool    `json:"required"`
	Status      string  `json:"status"`
	Code        string  `json:"code"`
	Summary     string  `json:"summary"`
	Remediation *string `json:"remediation"`
}

type Report struct {
	Overall   string    `json:"overall"`
	Version   string    `json:"version"`
	CheckedAt time.Time `json:"checked_at"`
	Checks    []Check   `json:"checks"`
}

type Status struct {
	SchemaVersion int       `json:"schema_version"`
	CheckedAt     time.Time `json:"checked_at"`
	Version       string    `json:"version"`
	Checks        []Check   `json:"checks"`
	Tailscale     string    `json:"tailscale"`
	UpdateStatus  string    `json:"update_status"`
}

type Policy struct {
	SecurityUpdates bool `json:"security_updates"`
	Weekday         int  `json:"weekday"`
	Hour            int  `json:"hour"`
	Minute          int  `json:"minute"`
}

func DefaultPolicy() Policy { return Policy{SecurityUpdates: true, Hour: 3} }
func (p Policy) Validate() error {
	if p.Weekday < 0 || p.Weekday > 6 || p.Hour < 0 || p.Hour > 23 || p.Minute < 0 || p.Minute > 59 {
		return errors.New("invalid maintenance schedule")
	}
	return nil
}
func (p Policy) Due(now time.Time) bool {
	return p.SecurityUpdates && int(now.Weekday()) == p.Weekday && now.Hour() == p.Hour && now.Minute() == p.Minute
}

func DecodeStrict(data []byte, target any) error {
	if len(data) > 65536 {
		return errors.New("JSON exceeds 64 KiB")
	}
	if err := uniqueJSON(json.NewDecoder(bytes.NewReader(data))); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return errors.New("invalid JSON or unknown fields")
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return errors.New("trailing JSON is not permitted")
	}
	return nil
}

// Reject duplicate fields at every nesting level; security policy must have one interpretation.
func uniqueJSON(decoder *json.Decoder) error {
	token, err := decoder.Token()
	if err != nil {
		return err
	}
	delimiter, ok := token.(json.Delim)
	if !ok {
		return nil
	}
	switch delimiter {
	case '{':
		seen := map[string]bool{}
		for decoder.More() {
			key, err := decoder.Token()
			if err != nil {
				return err
			}
			name, ok := key.(string)
			if !ok || seen[name] {
				return errors.New("duplicate or invalid JSON key")
			}
			seen[name] = true
			if err = uniqueJSON(decoder); err != nil {
				return err
			}
		}
	case '[':
		for decoder.More() {
			if err := uniqueJSON(decoder); err != nil {
				return err
			}
		}
	default:
		return errors.New("invalid JSON delimiter")
	}
	_, err = decoder.Token()
	return err
}

func Summarize(checks []Check, version string) Report {
	state := "ready"
	for _, check := range checks {
		if check.Status != "pass" && state != "blocked" {
			state = "ready_with_warnings"
		}
		if check.Required && check.Status != "pass" {
			state = "blocked"
		}
	}
	return Report{Overall: state, Version: version, CheckedAt: time.Now().UTC(), Checks: checks}
}

func NewCheck(id, label string, required, passed bool, code, summary, repair string) Check {
	state := "pass"
	var remediation *string
	if passed {
		code = "OK"
	} else {
		state = "warn"
		if required {
			state = "fail"
		}
		if repair != "" {
			remediation = &repair
		}
	}
	return Check{ID: id, Label: label, Required: required, Status: state, Code: code, Summary: summary, Remediation: remediation}
}

type Failure struct {
	Code        string `json:"code"`
	Message     string `json:"message"`
	Remediation string `json:"remediation,omitempty"`
}

func (f *Failure) Error() string                   { return f.Code + ": " + f.Message }
func Fail(code, message, remediation string) error { return &Failure{code, message, remediation} }

var releasePattern = regexp.MustCompile(`^v[0-9]+\.[0-9]+\.[0-9]+$`)

func ValidRelease(value string) bool { return releasePattern.MatchString(value) }

type Request struct {
	Action        string `json:"action"`
	Version       string `json:"version,omitempty"`
	PlanID        string `json:"plan_id,omitempty"`
	InstallDeps   bool   `json:"install_deps,omitempty"`
	Service       string `json:"service,omitempty"`
	Repair        string `json:"repair,omitempty"`
	Lines         int    `json:"lines,omitempty"`
	ManualFeature bool   `json:"manual_feature,omitempty"`
	BindAddress   string `json:"bind_address,omitempty"`
	Port          int    `json:"port,omitempty"`
}

func (r Request) Validate() error {
	actions := map[string]bool{"inspect": true, "plan": true, "install": true, "status": true, "logs": true, "restart": true, "repair": true, "backup": true, "check-updates": true, "update": true, "tailscale": true, "bind": true}
	if !actions[r.Action] {
		return errors.New("unsupported action")
	}
	if r.Version != "" && r.Version != "latest" && !ValidRelease(r.Version) {
		return errors.New("invalid release version")
	}
	if r.PlanID != "" && !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(r.PlanID) {
		return errors.New("invalid plan id")
	}
	if r.Action == "install" && r.PlanID == "" {
		return errors.New("installation requires a verified plan id")
	}
	if r.Service != "" && r.Service != "openlab-server" && r.Service != "openlab-worker" && r.Service != "openlab-web" && r.Service != "postgres" {
		return errors.New("invalid service")
	}
	if r.Action == "logs" && r.Service == "" {
		return errors.New("logs require a service")
	}
	if r.Repair != "" && r.Repair != "worker" && r.Repair != "migrations" && r.Repair != "secrets" {
		return errors.New("unknown repair recipe")
	}
	if r.Action == "repair" && r.Repair == "" {
		return errors.New("repair requires a recipe")
	}
	if r.Lines < 0 || r.Lines > 200 {
		return errors.New("log limit must be between 1 and 200")
	}
	if r.InstallDeps && r.Action != "install" && r.Action != "plan" && r.Action != "tailscale" {
		return errors.New("dependency permission is not valid for this action")
	}
	if r.ManualFeature && r.Action != "update" {
		return errors.New("manual feature approval is valid only for updates")
	}
	if r.BindAddress != "" || r.Port != 0 || r.Action == "bind" {
		ip := net.ParseIP(r.BindAddress)
		if (r.Action != "bind" && r.Action != "plan" && r.Action != "install") || ip == nil || ip.To4() == nil || (!ip.IsPrivate() && !ip.IsLoopback()) || r.Port < 1024 || r.Port > 65535 {
			return errors.New("invalid private binding")
		}
	}
	// Reject inert fields on the wrong action so future handlers cannot
	// accidentally broaden an old, already-authorized request.
	if r.Version != "" && r.Action != "plan" && r.Action != "install" && r.Action != "update" {
		return errors.New("version is not valid for this action")
	}
	if r.PlanID != "" && r.Action != "install" {
		return errors.New("plan id is not valid for this action")
	}
	if r.Service != "" && r.Action != "logs" {
		return errors.New("service is not valid for this action")
	}
	if r.Repair != "" && r.Action != "repair" {
		return errors.New("repair is not valid for this action")
	}
	if r.Lines != 0 && r.Action != "logs" {
		return errors.New("log limit is not valid for this action")
	}
	return nil
}

func (r Request) JSON() []byte { data, _ := json.Marshal(r); return data }
func SafeError(err error) Failure {
	var failure *Failure
	if errors.As(err, &failure) {
		return *failure
	}
	return Failure{Code: "OPERATION_FAILED", Message: "The bounded operation failed; inspect redacted diagnostics.", Remediation: "openlabctl doctor"}
}

func VersionTuple(version string) ([3]int, error) {
	var result [3]int
	if !ValidRelease(version) {
		return result, errors.New("invalid version")
	}
	_, err := fmt.Sscanf(version, "v%d.%d.%d", &result[0], &result[1], &result[2])
	return result, err
}

func Newer(candidate, installed string) bool {
	a, errA := VersionTuple(candidate)
	b, errB := VersionTuple(installed)
	if errA != nil || errB != nil {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return false
}
