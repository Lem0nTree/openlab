# OpenLab product MCP

`openlab` is the lab-data MCP. It is deliberately separate from
`openlab-installer`, which retains only its narrowly scoped installation and
diagnostic authority. Product MCP never exposes setup secrets, provider keys,
environment values, raw filesystem paths, arbitrary URLs, commands, or raw
attachments.

## Enable and connect

An owner enables **MCP integrations** in Settings. Existing installations are
disabled by default after upgrade. A public integration requires the verified
HTTPS public URL; HTTP is accepted only for a loopback callback during local
development. The service publishes protected-resource and authorization-server
metadata at `/.well-known/` and uses OAuth authorization-code flow with PKCE
S256. Access tokens expire after 15 minutes and refresh tokens rotate and
expire after 30 days.

Point a Streamable HTTP-capable harness at `https://LAB.example/mcp`. Register
the harness as a public client with its exact redirect URI, then authorize it
in the browser. Never put an access token in a harness configuration file.

For current HTTP-only LAN deployments, keep MCP transport off the network and
use an SSH tunnel/bridge to the lab host. Direct plain-HTTP product MCP is
intentionally refused; install a verified HTTPS public URL before using direct
network transport.

## Scope and review model

| Scope | Authority |
| --- | --- |
| `openlab:read` | Bounded lab records and redacted readiness |
| `openlab:write` | Additive drafts and captured records |
| `openlab:commit` | Confirmed inventory and review actions |
| `openlab:ai` | Explicit, reviewed provider-backed intelligence actions |

List tools are paginated (25 by default, 100 maximum); search is deterministic
and local. Direct additive writes require a caller-generated request ID.
Consequential changes first issue a one-use, five-minute confirmation receipt.
Applying the receipt reruns validation under the same database locks used by
the REST workflows, records the audit event, and is safely retryable. Owners
can inspect grants and revoke clients through the integrations API.

## Security and diagnostics

The endpoint caps request and tool output at 64 KiB and uses bearer grants tied
to the user, lab, client, and resource. Readiness reports MCP enablement and
whether HTTPS direct transport is eligible without disclosing configuration
values. A revoked or expired grant immediately fails token verification.
