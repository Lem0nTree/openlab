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

During installation, the terminal reports fixed phases for release validation,
local secret preparation, image pulling, service startup, and readiness. Image
pulls can take several minutes on a Raspberry Pi. The readiness meter checks
only the host services; it never waits for you to fill in the browser wizard.
If it reports a timeout, run `openlabctl doctor` for the named check rather
than assuming that entering owner details will unblock it.

While images are pulled or services start, sanitized Docker progress events are
printed live to the terminal on stderr. The final installation result remains
JSON on stdout so scripts and the scoped helper can parse it reliably.

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
4. **AI, optional:** select a provider/model, save a key if required, and test the
   endpoint. A successful model-list test does not prove every generation feature
   works. In Linux containers, a host-side Ollama can use
   `http://host.docker.internal:11434/v1` if Ollama is listening on an interface
   reachable by Docker. Container `localhost` is not the host.
5. **KiCad, optional:** configure a binary available inside the worker image and
   run its check. The standard image does not install KiCad automatically.
6. **Access and updates:** see host/Tailscale status and choose the local-time
   security-update maintenance window on managed installations.
7. **Readiness:** fix any required failures, or finish with clearly identified
   optional warnings. The page refreshes checks while open; no external ping or
   telemetry is sent.

Saved configuration survives page reloads. Return through **Settings → Setup &
readiness** at any time. The wizard never stores provider keys or bootstrap tokens
in local storage. Skipping AI or KiCad leaves manual inventory available.

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

Lifecycle commands return bounded JSON. `doctor` exits nonzero when required host
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
