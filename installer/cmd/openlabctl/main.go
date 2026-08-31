package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"

	"github.com/Lem0nTree/openlab/installer/internal/control"
	"github.com/Lem0nTree/openlab/installer/internal/mcp"
)

// Release builds inject the trusted public key; source builds fail closed when
// asked to download releases unless built with the maintainer's public key.
var version = "v0.2.0"
var releasePublicKey = ""

func main() { os.Exit(run()) }
func run() int {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	display := control.NewTerminalProgress(os.Stderr)
	defer display.Close()
	humanInstall := false
	engine := &control.Engine{Version: version, PublicKey: releasePublicKey, Progress: display.Update, Detail: display.Detail}
	finish := func(result any, err error) int {
		display.Close()
		if humanInstall && err == nil && !blockedResult(result) {
			fmt.Fprintln(os.Stdout, "OpenLab setup command completed. Run sudo openlabctl setup-link to continue.")
			return 0
		}
		return printResult(result, err)
	}
	if filepath.Base(os.Args[0]) == "openlabctl-helper" {
		if len(os.Args) != 1 {
			return 2
		}
		if engine.ServeHelper(ctx, os.Stdin, os.Stdout) != nil {
			return 1
		}
		return 0
	}
	if len(os.Args) < 2 {
		usage()
		return 2
	}
	command := os.Args[1]
	if command == "version" {
		fmt.Println(version)
		return 0
	}
	if command == "help" || command == "--help" {
		usage()
		return 0
	}
	if command == "mcp" {
		if len(os.Args) > 2 && os.Args[2] == "print-config" {
			fmt.Printf("%s\n", "{\"mcpServers\":{\"openlab\":{\"command\":\"/usr/local/bin/openlabctl\",\"args\":[\"mcp\"]}}}")
			return 0
			/* removed malformed legacy print
			   fmt.Println(`{"mcpServers":{"openlab":{"command":"/usr/local/bin/openlabctl","args":["mcp"]}}}`);return 0
			*/
		}
		if os.Geteuid() == 0 {
			return failure(control.Fail("MCP_ROOT_REFUSED", "Run the MCP server as your normal user; the scoped helper performs privileged actions.", "Use openlabctl mcp without sudo."))
		}
		return failure(mcp.Serve(ctx, os.Stdin, os.Stdout, version, func(ctx context.Context, request control.Request) (any, error) {
			if request.Action == "inspect" {
				return engine.Handle(ctx, request)
			}
			return control.CallHelper(ctx, request)
		}))
	}
	if command == "authorize" {
		return failure(engine.Authorize(ctx))
	}
	if command == "adopt" {
		flags := flag.NewFlagSet("adopt", flag.ContinueOnError)
		env := flags.String("env-file", "", "Absolute path to the original environment")
		project := flags.String("project", "deploy", "Existing Compose project name")
		selected := flags.String("version", "latest", "Signed target release")
		handover := flags.Bool("accept-handover", false, "Attest that the previous deployment controller has been paused")
		if flags.Parse(os.Args[2:]) != nil || flags.NArg() != 0 {
			return 2
		}
		result, err := engine.Adopt(ctx, *env, *project, *selected, *handover)
		return finish(result, err)
	}
	if command == "install" && len(os.Args) > 2 && os.Args[2] == "--from-source" {
		if len(os.Args) != 3 || os.Geteuid() == 0 {
			return failure(control.Fail("SOURCE_INSTALL_REFUSED", "Source installation runs as your normal user from a reviewed checkout.", "Run openlabctl install --from-source in the repository root without sudo."))
		}
		root, err := os.Getwd()
		if err != nil {
			return failure(err)
		}
		for _, path := range []string{"deploy/up.sh", "deploy/compose.yml", "backend/pyproject.toml", "web/package.json"} {
			if _, err = os.Stat(filepath.Join(root, path)); err != nil {
				return failure(control.Fail("SOURCE_CHECKOUT_REQUIRED", "This is not an OpenLab source checkout.", "Change to the reviewed repository root."))
			}
		}
		fmt.Fprintln(os.Stderr, "Building the reviewed local checkout using the existing source deployment. This mode has no privileged helper or automatic release updates.")
		build := exec.CommandContext(ctx, "/bin/sh", filepath.Join(root, "deploy/up.sh"), "--build", "-d")
		build.Stdin = os.Stdin
		build.Stdout = os.Stdout
		build.Stderr = os.Stderr
		if err = build.Run(); err != nil {
			return failure(err)
		}
		fmt.Println("Open http://HOST:3000/setup. Get the one-time token from the local server logs, then complete the browser wizard.")
		return 0
	}
	if command == "setup-link" {
		link, err := control.SetupLink()
		if err != nil {
			return failure(err)
		}
		fmt.Println(link)
		return 0
	}
	if command == "internal" {
		if os.Geteuid() != 0 || len(os.Args) != 3 {
			return 2
		}
		var result any
		var err error
		if os.Args[2] == "status" {
			result, err = engine.Doctor(ctx, true)
		} else if os.Args[2] == "scheduled-update" {
			result, err = engine.ScheduledUpdate(ctx)
		} else if os.Args[2] == "setup" {
			result, err = engine.ProcessSetup(ctx)
		} else {
			return 2
		}
		return finish(result, err)
	}
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	selectedVersion := flags.String("version", "latest", "Published release version")
	installDeps := flags.Bool("install-deps", false, "Explicitly permit supported dependency package installation")
	service := flags.String("service", "openlab-server", "Known Compose service for logs")
	lines := flags.Int("lines", 100, "Log lines (1-200)")
	writeStatus := flags.Bool("write-status", false, "Publish redacted status for the browser")
	feature := flags.Bool("feature", false, "Explicitly approve a compatible feature update")
	bind := flags.String("bind", "", "Private IPv4 address or 127.0.0.1")
	port := flags.Int("port", 3000, "Unprivileged web port")
	jsonOutput := flags.Bool("json", false, "Emit JSON instead of the interactive installation summary")
	args := os.Args[2:]
	recipe := ""
	if command == "repair" {
		if len(args) == 0 {
			return 2
		}
		recipe = args[0]
		args = args[1:]
	}
	if command == "network" {
		if len(args) == 0 || (args[0] != "tailscale" && args[0] != "bind") {
			return 2
		}
		command = args[0]
		args = args[1:]
	}
	if flags.Parse(args) != nil || len(flags.Args()) > 0 {
		return 2
	}
	action := command
	humanInstall = (action == "install" || action == "update") && !*jsonOutput && control.InteractiveTerminal(os.Stdout)
	if action == "doctor" {
		action = "status"
	}
	request := control.Request{Action: action}
	if action == "install" || action == "plan" {
		request.Version = *selectedVersion
		request.InstallDeps = *installDeps
	}
	if action == "tailscale" {
		request.InstallDeps = *installDeps
	}
	if action == "logs" {
		request.Service = *service
		request.Lines = *lines
	}
	if action == "repair" {
		request.Repair = recipe
	}
	if *feature {
		request.ManualFeature = true
	}
	if *bind != "" {
		request.BindAddress = *bind
		request.Port = *port
	}
	dispatch := func(request control.Request) (any, error) {
		if os.Geteuid() == 0 {
			return engine.Handle(ctx, request)
		}
		return control.CallHelper(ctx, request)
	}
	if action == "install" {
		if os.Geteuid() != 0 {
			if _, err := os.Stat(control.HelperPath); os.IsNotExist(err) {
				fmt.Fprintln(os.Stderr, "OpenLab needs a one-time scoped host grant. sudo will ask for your local password; it is never sent to AI.")
				executable, _ := os.Executable()
				grant := exec.CommandContext(ctx, "sudo", executable, "authorize")
				grant.Stdin = os.Stdin
				grant.Stdout = os.Stderr
				grant.Stderr = os.Stderr
				if err = grant.Run(); err != nil {
					return failure(control.Fail("AUTHORIZATION_REQUIRED", "The scoped grant was not completed.", "Run sudo openlabctl authorize."))
				}
			}
		}
		planRequest := request
		planRequest.Action = "plan"
		result, err := dispatch(planRequest)
		if err != nil {
			return failure(err)
		}
		data, _ := json.Marshal(result)
		var plan control.Plan
		if json.Unmarshal(data, &plan) != nil || plan.ID == "" {
			return failure(control.Fail("PLAN_INVALID", "The installer plan could not be read.", "Run openlabctl plan."))
		}
		request.PlanID = plan.ID
		request.Version = plan.Version
		fmt.Fprintf(os.Stderr, "Installing verified OpenLab %s. Existing secrets and data will be preserved.\n", plan.Version)
	}
	if *writeStatus && action == "status" {
		if os.Geteuid() != 0 {
			return failure(control.Fail("ROOT_REQUIRED", "Publishing the local status file requires sudo.", "Run sudo openlabctl doctor --write-status."))
		}
		result, err := engine.Doctor(ctx, true)
		return finish(result, err)
	}
	if action == "inspect" {
		result, err := engine.Handle(ctx, request)
		return finish(result, err)
	}
	result, err := dispatch(request)
	if action == "install" && control.SafeError(err).Code == "UPDATE_REQUIRED" {
		result, err = dispatch(control.Request{Action: "update", Version: request.Version, ManualFeature: true})
	}
	return finish(result, err)
}

func printResult(result any, err error) int {
	if result != nil {
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetIndent("", "  ")
		_ = encoder.Encode(result)
	}
	if err == nil && blockedResult(result) {
		return 2
	}
	return failure(err)
}
func blockedResult(result any) bool {
	if report, ok := result.(control.Report); ok {
		return report.Overall == "blocked"
	}
	if report, ok := result.(map[string]any); ok {
		return report["overall"] == "blocked"
	}
	return false
}
func failure(err error) int {
	if err == nil {
		return 0
	}
	_ = json.NewEncoder(os.Stderr).Encode(control.SafeError(err))
	return 1
}
func usage() {
	fmt.Println(`OpenLab installer and diagnostics
  openlabctl install [--version vX.Y.Z] [--install-deps] [--bind PRIVATE_IP --port 3000] [--json]
  openlabctl plan [--version vX.Y.Z] [--install-deps]
  openlabctl inspect | doctor | status | restart | backup | check-updates | update
  openlabctl logs --service openlab-worker --lines 100
  openlabctl repair worker|migrations|secrets
  openlabctl network tailscale [--install-deps]
  openlabctl network bind --bind PRIVATE_IP --port 3000
  openlabctl update --feature
  openlabctl install --from-source  (normal user, reviewed repository root)
  sudo openlabctl adopt --accept-handover --env-file /home/pi/openlab/.env --project deploy
  sudo openlabctl setup-link
  sudo openlabctl authorize
  openlabctl mcp [print-config]
Install/update use short interactive summaries; --json or redirected stdout keeps JSON.
Other lifecycle results and MCP use JSON; diagnostics never include secrets.`)
}
