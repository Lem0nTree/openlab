# Install and configure OpenLab

OpenLab runs on a 64-bit Raspberry Pi, Debian/Ubuntu server, or another Linux
AMD64/ARM64 host with Docker Engine and Compose **2.24.4 or newer**. Use a private
LAN or Tailscale; do not forward the plain HTTP application port to the internet.
Allow at least 2 GiB of free disk space before pulling images (more is recommended
for updates and backups), and preferably 1 GiB or more RAM. Small Pis can be slow.

## Choose an installation path

The signed-release path below requires a published release containing `install.sh`,
the two Linux binaries, `release.json`, its signature, and the Compose bundle.
This branch adds that release pipeline; it does **not** itself publish those assets.
Until maintainers publish the first signed release, use the source path below.

### Signed release: download, inspect, install

Download `install.sh` from the chosen version on the repository's GitHub Releases
page, inspect it, then run it as your normal Linux user:

```sh
sh install.sh --install-deps
sudo openlabctl setup-link
```

Omit `--install-deps` when Docker and Compose are already installed. On supported
Debian/Ubuntu/Pi OS hosts, that flag explicitly permits adding the official Docker
package repository and installing Docker packages. It does not change firewall,
router, DNS, or public ingress settings. Other distributions require manual Docker
installation. The bootstrap asks for a local sudo password to install or refresh
the scoped helper from the verified binary; neither the password nor owner
credentials go to an AI.

The bootstrap pins an exact version, verifies the signed release metadata and
binary checksum, then installs the static CLI. The inspected bootstrap script
and its embedded public key are the initial trust decision. Later downloads are
verified by that key; unsigned or altered releases fail closed.

During installation, an interactive terminal shows a single refreshing status
line with a spinner, an activity bar, the current phase, and elapsed seconds.
Docker events update that line instead of printing every container transition.
The animation continues even when Docker or a diagnostic call is quiet. Image
pulls can take several minutes on a Raspberry Pi. The activity bar is not a
completion percentage. Readiness shows `DB`, `API`, `Web`, and `Worker` as `wait`
or `ok`, based on service/image checks, HTTP routes, and the worker heartbeat.
It never waits for you to fill in the browser wizard.
If it reports a timeout, run `openlabctl doctor` for the named check rather
than assuming that entering owner details will unblock it.

Progress stays on stderr. Redirected logs (including MCP captures) and `TERM=dumb` terminals receive
plain phase messages and a heartbeat every 30 seconds, without ANSI animation
or a line for every Docker event. The scoped helper always returns JSON on
stdout. Interactive `install` and `update` commands finish with a short summary;
use `--json` for the full result. Redirecting stdout also preserves the JSON
result automatically. Errors keep their diagnostic code and remediation.

Open the URL printed by `setup-link` in a browser on the same trusted network.
Its fragment contains the one-time owner setup token: treat the whole link as a
secret. The browser removes the fragment from history and does not put it into
API URLs. Do not paste the link into AI chat, issue reports, or shared logs.

To choose a different private listener during installation:

```sh
openlabctl install --bind 192.168.1.40 --port 3000
```

The default is a detected private IPv4 address plus loopback, not all interfaces.
If detection selects the wrong interface, choose the address explicitly. A host
with no private interface defaults to loopback; use an SSH tunnel to reach it.

### Source checkout: available without published artifacts

```sh
git clone https://github.com/Lem0nTree/openlab.git
cd openlab
sh deploy/up.sh --build -d
docker compose --env-file .env -f deploy/compose.yml logs openlab-server
```

Open `http://HOST:3000/setup` and use the token printed in the local server log.
This path builds locally, keeps `.env` in the checkout, and preserves the existing
Compose project/volumes. It is not managed by the signed-release updater. If you
have built or installed the CLI, `openlabctl install --from-source` from a reviewed
checkout is a convenience wrapper for the same build, without sudo or MCP.

Source Compose preserves the historical all-interface listener by default. Set
`OPENLAB_BIND_ADDRESS` in `.env` before starting to limit exposure. Never expose
either installation path directly to an untrusted network.

## Complete the browser wizard

1. **Create owner:** enter the local setup token, lab name, owner name/email, and
   a password of at least 12 characters. A session is created immediately.
2. **Lab:** confirm the name and units.
3. **Network:** save the canonical browser address while visiting that address.
   This verifies the authenticated browser origin, not reachability from every
   device. QR labels use the saved address.
4. **AI, optional:** choose **Try with OpenRouter** to select its free-model router
   (`openrouter/free`), or select another provider/model. Add a key if required,
   then choose **Connect and continue**. This saves, enables, tests the endpoint,
   and verifies AI readiness before advancing. **Skip AI** discards form changes
   and disables processing. A successful model-list test does not prove every generation feature
   works. In Linux containers, a host-side Ollama can use
   `http://host.docker.internal:11434/v1` if Ollama is listening on an interface
   reachable by Docker. Container `localhost` is not the host.
5. **KiCad, optional:** use **Install KiCad and connect…** for the host installation
   guide, then **Connect KiCad** to save and check the worker executable.
   KiCad is needed for schematic electrical rules checks, not manual inventory.
   It does not inspect physical wiring or certify circuit safety.
6. **Access and updates:** use **Install Tailscale and connect…** for installation
   and authorization instructions, or skip. The buttons provide a host-terminal
   handoff; the web app has no host shell or Docker socket. Only fresh installer
   evidence is reported as connected. Choose the local-time security-update window
   on managed installations.
7. **Product MCP, optional:** enable the integration, copy its HTTPS endpoint into
   your MCP client, and authorize it in the browser. Enablement is distinct from
   client authorization and recorded use. See [Product MCP](PRODUCT_MCP.md).
8. **Readiness:** fix any required failures, or finish with clearly identified
   optional warnings. The page refreshes checks while open. Production installations
   register once with OpenLab's telemetry endpoint; after this recap, the default-on
   telemetry setting sends a daily pseudonymous aggregate, including zero-activity days.
   It never includes inventory, captures, lab names, email addresses, or provider settings.
   Change or delete telemetry history later in **Settings → Privacy and data**.

Saved configuration survives page reloads. Return through **Settings → Setup &
readiness** at any time. The wizard never stores provider keys or bootstrap tokens
in local storage. Skipping AI or KiCad leaves manual inventory available.

### Add KiCad to a source-built worker

Set `OPENLAB_INSTALL_KICAD=1` in the root `.env` file, then run from the checkout:

```sh
sh deploy/up.sh --build --no-deps -d openlab-worker
```

The optional build argument installs the distribution's KiCad package inside the
worker image. It leaves the server image lightweight and retains KiCad across
worker recreations and subsequent builds using that `.env` file. Package downloads
can be large and restart the worker; let active jobs finish first. Use
`kicad-cli` in onboarding and connect after the rebuild completes.

This option is for source builds. Signed-release installations need a supported
worker image supplied through their release process; the browser cannot replace
signed images. Installing KiCad on the host alone does not make it available to
the worker. Connection checks detect the binary version; a real schematic check
is still needed to validate ERC compatibility.

## Everyday commands

```sh
openlabctl inspect
openlabctl plan
openlabctl doctor
openlabctl logs --service openlab-worker --lines 100
openlabctl restart
openlabctl repair worker
openlabctl repair migrations
openlabctl repair secrets
openlabctl backup
openlabctl check-updates
openlabctl update
openlabctl update --feature
openlabctl network bind --bind 192.168.1.40 --port 3000
openlabctl network tailscale --install-deps
```

Lifecycle commands return bounded JSON when redirected or requested with `--json`.
Interactive installation/update summaries stay short. `doctor` exits nonzero when required host
checks fail. `repair secrets` validates/preserves existing encryption and session
keys; it never replaces keys needed to decrypt existing data. The migration repair
temporarily stops application services and runs the release's Alembic migrations.

`network tailscale` optionally installs Tailscale, reports an interactive login
requirement, and adds the host's private tailnet address to OpenLab's listener.
It does not configure Serve, Funnel, public TLS, or overwrite unrelated tailnet
settings. Complete login yourself and rerun the command, then visit that address
and save it in the Network step if it should become the canonical URL.

## Files, backups, and update recovery

Managed installations use:

| Path | Purpose |
| --- | --- |
| `/opt/openlab/deploy/` | Verified Compose release files |
| `/etc/openlab/openlab.env` | Root-readable persistent secrets |
| `/etc/openlab/installation.json` | Root-owned release/project identity |
| `/etc/openlab/network.yml` | Explicit private listener mappings |
| `/var/lib/openlab-installer/backups/` | Root-readable DB, attachments, env and receipts |
| `/var/lib/openlab-installer/control/status.json` | Redacted diagnostics, mounted read-only |
| `/var/lib/openlab-installer/control/policy/policy.json` | Strict app-writable maintenance policy |

Backups stop application writers, dump PostgreSQL, copy attachments and the
original environment/config, then restart services. Keep encrypted off-host
copies under your own backup policy; the installer does not upload them or delete
old backups automatically. Check disk space before upgrades.

New managed installations default to security-only updates on Sunday at 03:00 in
the **server's local timezone**. A systemd timer checks the window; hosts without
systemd require manual updates. Eligibility requires a signed security release,
an explicit unattended-safe declaration, compatible schema metadata, and a
sufficiently new installer. Feature updates require `update --feature` and still
require rollback-compatible migrations. Breaking upgrades require manual review.

Every applied update first makes a backup. Failed startup/readiness restores
previous image/configuration identities and reports the failure. **Database
migrations are not reversed automatically.** If image rollback fails, stop and
inspect the report; do not delete volumes, regenerate encryption keys, or restore
a database over a running application. A deliberate database restore must use the
matching backup, original encryption key, and compatible images.

The status timer refreshes host diagnostics every five minutes. A stale status
blocks managed readiness rather than claiming the host is healthy. Run
`sudo openlabctl doctor --write-status` to refresh immediately.

## Adopt an existing Pi/source installation

Do not let two deployment controllers operate the same Compose project. Leave
the existing Pi CI workflow alone unless you intentionally hand over that host.
Adoption is a local-administrator command, never an MCP operation:

```sh
sudo openlabctl adopt --accept-handover \
  --env-file /home/pi/openlab/.env --project deploy --version vX.Y.Z
```

Replace the example version with a published compatible release. First pause the
previous deployment controller yourself and verify the original project name
with Docker's Compose labels. The historical `deploy/up.sh` default project is
`deploy`, not the repository directory name. The command refuses ambiguous
containers, custom mounts, mismatched worker/server images, missing original
secrets, or a schema not explicitly supported by the signed release. It reads
literal env values only; interpolated/custom env files require manual review.

The original checkout and `.env` are not changed. The selected named volumes are
reused, not copied over or removed. Current image IDs are recorded for recovery,
then the compatible release uses the normal backup/update path. Adopted hosts
start with scheduled updates disabled; enable them deliberately in the wizard.
An interrupted adoption leaves a managed recovery record: inspect `doctor` and
the backup before retrying an update. Do not rerun another deployment controller
against those volumes simultaneously.

## Diagnose a blocked setup

| Code | Next action |
| --- | --- |
| `DOCKER_UNAVAILABLE` / `COMPOSE_UNAVAILABLE` | Install/enable Docker and Compose ≥2.24.4; check access. |
| `PORT_IN_USE` | Select a free port and an address assigned to this host. |
| `EXISTING_DATA_NEEDS_ENV` | Restore the original `.env`; do not rotate keys. |
| `RELEASE_TRUST_UNCONFIGURED` | Use an official signed binary or the source-build path. |
| `RELEASE_SIGNATURE_INVALID` | Stop; obtain the official release, never bypass verification. |
| `SERVICE_NOT_READY` / `READINESS_TIMEOUT` | Inspect the named service's bounded logs and disk space. |
| `WORKER_UNAVAILABLE` | Check/restart the worker; heartbeat must match this release. |
| `MIGRATIONS_PENDING` | Back up first, inspect migration logs, run the bounded repair. |
| `URL_NOT_VERIFIED` | Visit the intended URL and save it in Network. |
| `HOST_STATUS_STALE` | Check the status timer; refresh host diagnostics locally. |
| `AI_NOT_TESTED` / `KICAD_NOT_VERIFIED` | Test the optional integration or proceed with warnings. |

Readiness is a point-in-time check, not a promise that a host, network, provider,
or physical device can never fail. There are no synthetic success indicators.
