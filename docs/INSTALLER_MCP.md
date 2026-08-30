# Let an AI help install OpenLab

The installer exposes a local stdio MCP server: `openlabctl mcp`. It shares the
same typed lifecycle engine as the CLI. It is not a general-purpose server shell.

## One-time host authorization

Install the verified Linux CLI on the target machine. In a real local/SSH terminal,
as the intended normal user, run:

```sh
sudo openlabctl authorize
openlabctl mcp print-config
```

The grant installs a root-owned helper and a sudoers rule for **that exact helper
with no command-line arguments**. The helper reads one closed JSON request from
stdin, validates every field, uses fixed paths/commands, and serializes mutations.
The MCP process runs as your normal user; running the MCP server as root is refused.
The grant persists until a local administrator removes its user-specific
`/etc/sudoers.d/openlab-UID` rule. Never grant `NOPASSWD: ALL` for this workflow.

An AI cannot perform the initial privileged trust decision for you. After it is
granted, ask: “Inspect this host, plan the OpenLab installation, install the verified
release, and report readiness or actionable errors. Keep passwords and setup tokens
out of chat.” Package installation still needs the explicit `install_deps` flag.

## Client configuration

For clients accepting the common JSON MCP configuration:

```json
{
  "mcpServers": {
    "openlab": {
      "command": "/usr/local/bin/openlabctl",
      "args": ["mcp"]
    }
  }
}
```

For a Linux host reached from a desktop client, use SSH stdio with an already
configured host alias/key. Do not allocate a TTY or put credentials in the config:

```json
{
  "mcpServers": {
    "openlab-pi": {
      "command": "ssh",
      "args": ["-T", "-o", "BatchMode=yes", "openlab-pi", "/usr/local/bin/openlabctl", "mcp"]
    }
  }
}
```

For TOML-based clients, translate the same executable/arguments:

```toml
[mcp_servers.openlab]
command = "/usr/local/bin/openlabctl"
args = ["mcp"]
```

The target host must already be trusted in SSH known_hosts. Keep shell login
banners off stdout for noninteractive SSH, because stdio carries only MCP JSON.
Restart/reload your client's MCP configuration according to that client's UI.

## Tool boundaries

| Tool | Capability |
| --- | --- |
| `inspect_host` | Architecture, resources, Docker/Compose, scheduler |
| `plan_install` | Verify release and return the exact installation plan ID |
| `apply_install` | Recompute and apply that plan; changed plans are rejected |
| `get_status` | Host/service readiness with stable diagnostic codes |
| `get_logs` | One enumerated service, at most 200 lines, secret redaction |
| `restart_services` | Restart the selected installation's fixed service set |
| `repair_installation` | Only `worker`, `migrations`, or `secrets` recipes |
| `configure_tailscale` | Private tailnet access, with interactive login handoff |
| `backup_installation` | Local backup; returns a receipt, never backup contents |
| `check_updates` | Signed release eligibility |
| `apply_security_update` | Eligible security release, backup, checks and rollback |

There are no tools for arbitrary commands, package names, target directories,
URLs, Compose files, root shells, secret reads, owner/password entry, destructive
volume operations, source builds, adoption, or feature upgrades. The browser has
no Docker socket. The only app-writable host control is a strict schedule policy;
it cannot submit commands to the privileged helper.

The AI returns the token-free setup address. Run `sudo openlabctl setup-link`
yourself and enter owner credentials/provider keys directly in the browser. The
AI may explain a diagnostic or invoke a bounded repair; a successful host check
does not substitute for completing browser configuration.

See [Installation](INSTALLATION.md) for supported hosts, recovery, and status codes.
