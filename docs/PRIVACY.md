# OpenLab Privacy Policy

Last updated: August 31, 2026

Questions or requests about this policy can be sent to
[support@openlab.tools](mailto:support@openlab.tools).

## Public website

The public website provides product and installation information. It does not
provide hosted OpenLab accounts or collect users' lab inventory. If optional
Plausible analytics is enabled, it collects privacy-focused, aggregated
site-usage measurements according to the configured service. The website does
not use advertising trackers.

## Self-hosted application data

OpenLab runs on infrastructure chosen by the person or organisation that
installs it. That operator controls the inventory, accounts, attachments,
backups, network logs, and retention practices in that installation. OpenLab
does not send this data to OpenLab-operated servers as part of normal
operation, except for the narrowly-scoped pseudonymous telemetry described
below when it is enabled.

## Pseudonymous product telemetry

Production installations make one non-blocking registration request to
`https://telemetry.openlab.tools/v1/installations/register`. After the owner
has reached the onboarding readiness recap, usage reporting is on by default.
It sends one report for the preceding UTC day (including zero-activity days),
at a deterministic time within the hour. The report contains a stable random
installation ID, OpenLab version, CPU platform, UTC activity date, aggregate
counts of completed inbox processing, confirmed candidates, Things and Projects
created, plus whether email intake is configured. It does not include inventory
content, captures, lab names, account email addresses, user IDs, provider or AI
configuration, request IP addresses, or user agents.

The stable ID makes this **pseudonymous**, rather than anonymous, telemetry.
The receiver retains per-installation daily records for at most 24 months and
then retains only non-linkable aggregate rollups. You can disable future reports
and request deletion of remote per-installation history at **Settings → Privacy
and data**. Disabling telemetry cancels queued daily activity reports. A restored
database remains the same installation; create a fresh identity only before
operating a cloned restore as a separate installation.

The legal basis and the unconditional registration request require controller
and legal review before a telemetry-enabled production release. This policy does
not claim GDPR compliance or identify a controller that has not been verified.

## Optional product updates

The owner-setup newsletter checkbox is unchecked. If selected, OpenLab stores a
separate consent receipt with its timestamp and notice version, then queues a
subscription request independently of account creation. Newsletter contact data
never appears in telemetry reports. You can withdraw this preference in
**Settings → Privacy and data**; delivery is asynchronous and a receiver outage
does not prevent local setup.

## Optional AI processing

AI features are optional. Before processing a capture or inventory profile,
OpenLab identifies whether the configured endpoint is local or external. If an
operator chooses an external provider, relevant content may be sent to that
provider under its terms and privacy policy. Configure only providers you
trust.

## Support email

When you email support, we use your message and contact details to respond,
troubleshoot, and manage licensing requests. Do not send setup links,
passwords, API keys, backups, or other secrets by email.

## Your choices

You can run OpenLab without AI, use a local compatible provider, disable
optional public-site analytics where applicable, and manage or delete data from
your own installation. The current public version of this policy is at
[openlab.tools/privacy](https://openlab.tools/privacy).
