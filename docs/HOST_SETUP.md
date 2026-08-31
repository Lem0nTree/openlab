# Host setup from onboarding

The signed Linux installer runs `openlab-setup.timer` every ten seconds on
systemd hosts. Onboarding can ask it to refresh diagnostics, install the optional
KiCad worker, install/connect Tailscale, or enable private HTTPS. Source checkouts
and older installers without this service show an explicit upgrade/manual path.
Run the current signed bootstrap once to upgrade the CLI, helper, and services;
updating only application containers does not install the host setup service.

KiCad uses `images.kicad_worker` in the signed release manifest. Release CI builds
the worker for AMD64 and ARM64 with `OPENLAB_INSTALL_KICAD=1`, installs Debian's
KiCad package without recommended libraries, and checks the CLI before signing.
This is a headless worker, not a guarantee of a small image: KiCad's required
runtime dependencies are still included. The installer pulls an immutable digest,
restarts only the worker, checks `kicad-cli --version` and service readiness, and
restores the previous worker configuration if activation fails. The browser then
saves `kicad-cli` and runs the normal lab-scoped capability check. Updates retain
the selected KiCad worker variant; releases without it cannot replace an enabled
variant. Older CLIs that cannot parse the optional image field need the current
signed bootstrap before upgrading to these manifests.

Tailscale status comes from `tailscale status --json` on the host, not from the
application container or a cached "not installed" default. Only a missing binary
is reported as not installed. A daemon error, stale report, or inaccessible host
is unknown/unavailable. A stopped or unapproved device needs authorization.
Connecting reuses an existing installation. If login is necessary, finish
`sudo tailscale up` in the host terminal; authorization URLs are never written
to the shared control directory or exposed to the browser.

Private HTTPS uses Tailscale Serve, not SSH certificates or self-signed TLS.
The installer configures the fixed local web port and verifies a trusted TLS
connection and application health. Existing Serve/Funnel configurations are not
overwritten; public Funnel is never enabled. Enable MagicDNS and HTTPS in the
tailnet's DNS settings if requested. Tailscale manages the certificate; the
machine DNS name appears in public certificate transparency logs. See
[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) and
[HTTPS certificates](https://tailscale.com/docs/how-to/set-up-https-certificates).

After HTTPS succeeds, open the secure address, sign in, and choose **Use this
HTTPS address** in Product MCP. Verification uses the authenticated browser's
actual origin; the server does not fetch arbitrary owner-entered URLs. Enable
Product MCP and authorize the client separately. A private tailnet URL works
only for clients that can reach that tailnet; a cloud client may need a separately
configured public HTTPS endpoint. A certificate alone does not grant access.

## Security boundary

`POST /api/v1/settings/host-setup` requires the owner session and CSRF token. It
accepts only four action names, with no command, image, path, URL, or shell text.
The API atomically publishes one bounded request in the existing writable policy
directory. The installer claims it into a root-only directory, rejects symlinks,
non-regular files, expanded JSON, expired/future requests and replayed IDs, and
serializes actions with the same host lock used by CLI/MCP mutations. The web
container has no Docker socket or privileged host mount. The owner therefore
authorizes only these fixed setup operations, never unrestricted root execution.

The root-owned `setup-status.json` contains bounded progress and no secrets.
Only the owner API exposes it. Docker output and Tailscale login URLs are not
published there. New requests fail closed when the host service is unavailable.
After interruption, inspect `openlabctl doctor` and the operation status before
retrying. Services are never reported connected solely because an action was
queued, an image was pulled, or a certificate command exited successfully.
