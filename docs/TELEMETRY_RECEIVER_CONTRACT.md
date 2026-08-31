# OpenLab telemetry receiver contract (v1)

This is the implementation contract for a receiver such as
`Lem0nTree/openlab-dashboard`. It describes what released OpenLab clients send
to `https://telemetry.openlab.tools/v1`. Treat the data as **pseudonymous**:
the client keeps a stable random installation ID, enabling longitudinal
reporting. It is not anonymous telemetry.

## Delivery rules

- Only production builds with a semantic version (for example `v1.2.0`) send
  telemetry. Development and test builds never contact the public receiver.
- A persistent installation creates the random `installation_id` and bearer
  credential once. They survive restarts, upgrades, and normal restores.
- Registration is queued during first production startup and is non-blocking.
  A receiver outage must never prevent installation, account creation,
  onboarding, or normal OpenLab operation.
- Requests use JSON with `Content-Type: application/json` where there is a
  body. The client retries failed deliveries with capped exponential backoff.
- `Idempotency-Key` is sent for registration and authenticated deliveries.
  The receiver must make retries safe.
- Daily activity starts only after the onboarding readiness recap has been
  completed and the readable telemetry control has been shown. It is default-on
  but can be disabled in OpenLab Settings.
- Activity is for the previous UTC day, including zero-activity days. Client
  submissions are deterministically spread across the first hour of the day.

## Public registration

`POST /v1/installations/register`

Headers:

```http
Content-Type: application/json
Idempotency-Key: register:<installation_id>
```

Body:

```json
{
  "schema_version": 1,
  "installation_id": "client-generated-random-id",
  "installation_token": "client-generated-random-bearer-credential",
  "app_version": "v1.2.0",
  "platform": "arm64"
}
```

The client generates both ID and credential. Store a keyed HMAC of the
installation ID and a timing-safe hash of the bearer credential; never store
either raw value. A successful `2xx` response acknowledges registration. A
repeat with the same identity/credential/idempotency key must be safe, while a
credential replacement attempt must be rejected.

## Daily activity

`PUT /v1/activity`

Headers:

```http
Content-Type: application/json
Authorization: Bearer <installation_token>
Idempotency-Key: activity:<installation_id>:<YYYY-MM-DD>
```

Body:

```json
{
  "schema_version": 1,
  "event_id": "activity:client-generated-random-id:2026-08-31",
  "installation_id": "client-generated-random-id",
  "app_version": "v1.2.0",
  "platform": "arm64",
  "activity_day": "2026-08-31",
  "inbox_processed": 3,
  "components_confirmed": 18,
  "things_created": 5,
  "projects_created": 1,
  "email_intake_enabled": false
}
```

All four counters are non-negative bounded integers. The receiver must validate
schema version, semantic version, platform (`arm64`, `amd64`, or `other`), UTC
date, matching authenticated installation ID, and a unique `event_id`. Accept
duplicates idempotently rather than double-counting.

Field definitions:

| Field | Meaning |
| --- | --- |
| `inbox_processed` | `inbox.process` jobs completed on `activity_day`. |
| `components_confirmed` | `inbox.candidate_confirmed` audit events on that day. |
| `things_created` | Thing rows created on that day. |
| `projects_created` | Project rows created on that day. |
| `email_intake_enabled` | `false` until OpenLab has a real email-intake setting; then its direct setting value. It is not inferred from inbox content. |

## Telemetry preference and deletion

`PUT /v1/preferences` is authenticated with the bearer header and sends:

```json
{
  "installation_id": "client-generated-random-id",
  "usage_enabled": true,
  "disclosure_version": "2026-08-31"
}
```

`DELETE /v1/history` is authenticated with the bearer header and has no body.
It requests deletion of this installation's remote detailed history. Both routes
need idempotent `2xx` handling.

## Separate newsletter channel

Newsletter data is never included in registration, preference, or activity
payloads. The first-owner checkbox is unchecked and account creation commits
locally before remote delivery.

`POST /v1/subscriptions` is authenticated and sends:

```json
{
  "email": "owner@example.com",
  "consented_at": "2026-08-31T12:00:00+00:00",
  "consent_version": "2026-08-31",
  "source": "owner_setup"
}
```

The receiver should encrypt the email at rest and return:

```json
{ "subscription_token": "random-unsubscribe-token" }
```

`DELETE /v1/subscriptions/{subscription_token}` is authenticated with the
installation bearer credential. It must be idempotent and remove or mark the
subscription unsubscribed without linking subscriber data into telemetry views.

## Data that must never be accepted or retained

Do not collect or log raw installation IDs, bearer credentials, request IPs,
user agents, request bodies, lab names, inventory/components, captures,
attachments, email content, user IDs, provider/API configuration, AI prompts,
file paths, or hardware identifiers. Enforce a small JSON body limit and redact
structured observability.

Per-installation daily records must be deleted after 24 months. Retain only
non-linkable aggregate rollups after that point. The dashboard must protect its
own endpoints/UI with Cloudflare Access and expose only receiver aliases, never
raw identifiers, credentials, subscriber emails, or unsubscribe tokens.
