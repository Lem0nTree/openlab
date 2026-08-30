# Installer development and release contract

The Go module has no third-party dependencies. Build on Linux with Go 1.24 or
newer (CI uses 1.27.0):

```sh
cd installer
go test ./...
go vet ./...
CGO_ENABLED=0 go build -o openlabctl ./cmd/openlabctl
```

A development binary intentionally has no production release trust key. It can
inspect hosts, serve MCP diagnostics, and wrap a reviewed source build; attempts
to download/apply signed releases fail closed until built with the official key.

## Maintainer release setup

The manual **Signed installer release** workflow is separate from the existing
CI-gated Pi deployment. It does not SSH to the Pi or reuse its build runner.
Do not dispatch it merely to test a branch: it publishes GHCR images and GitHub
release assets. Configure a protected `release` environment with required human
reviewers and restricted release branches/tags, plus:

- Secret `OPENLAB_RELEASE_SIGNING_SEED`: base64 of the existing 32-byte Ed25519 seed.
- Variable `OPENLAB_RELEASE_PUBLIC_KEY`: base64 of its matching 32-byte public key.
- Variable `OPENLAB_POSTGRES_IMAGE`: a reviewed immutable
  `pgvector/pgvector@sha256:...` multiarchitecture index digest.

Generate/store production signing material through your secret-management
process, not in source, logs, test fixtures, or an AI conversation. The packager
requires the seed and configured public key to match; it never invents a trust key.
Keep key rotation a deliberate installer trust-root update, not a remote policy.

Review `release-policy.json` for every release. The `rollback_compatible_schemas`
list is an explicit assurance that the previous application can run after the
new migrations; it must be backed by migration and rollback tests. The initial
policy is deliberately feature/manual; its `0009` compatibility is exercised
with real owner/session data in the disposable Compose release gate. Security releases require `classification:
security` and `unattended_safe: true`; neither a version number nor a tag name
alone grants unattended-update permission.

Create a reviewed version tag, run the workflow with that exact tag, and inspect
the tests/artifacts before approving publication. It builds both architecture
images, injects the public key into static binaries, signs exact JSON manifest
bytes, and attaches versioned assets. Existing releases are never overwritten.
Make the GHCR packages publicly readable if supporting anonymous public installs.

Artifact contracts live in `internal/control/release.go`; unknown fields,
duplicate JSON keys, mutable image references, unapproved repositories, bad
signatures, wrong hashes, and unsafe bundle entries fail closed. Compose files
are executable release authority and require the same review as application code.

## Verification boundaries

Unit tests cover request/manifest trust, archive extraction, secret preservation,
strict policies, redaction, and MCP framing. They do not prove Docker deployment,
APT installation, Tailscale login, sudoers setup, or end-to-end browser behavior.
Use isolated hosts/containers and disposable volumes for integration tests.
Never run lifecycle tests against `/home/pi/openlab` or production volumes.

User guides: [Installation](../docs/INSTALLATION.md) and
[Installer MCP](../docs/INSTALLER_MCP.md).
