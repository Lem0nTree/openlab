# Automatic AWS-to-Pi deployment

OpenLab deploys an exact merge commit only after the repository's `CI` workflow
succeeds on `main`. A GitHub-hosted gate confirms that the commit is the result
of a merged pull request. The build and deployment job then runs on the private
ARM64 runner labelled `openlab-build` on `aws-t3mini`.

The AWS host builds the backend and web images natively, transfers them to the
Pi over Tailscale, compares their image IDs, and refreshes only the OpenLab
Compose project. `/home/pi/openlab/.env`, PostgreSQL data, attachments, named
volumes, and unrelated containers are not replaced or removed.

## Host setup

The persistent host setup is intentionally outside GitHub secrets:

- Register an ARM64 repository runner named `aws-t3mini-openlab` as the
  `ubuntu` user with the labels `linux`, `arm64`, and `openlab-build`.
- Join AWS to the Pi's Tailscale network and keep MagicDNS enabled.
- Generate `/home/ubuntu/.ssh/openlab_pi_deploy` on AWS. Add only its public key
  to the Pi's `pi` account.
- Configure the AWS SSH alias `openlab-pi` with the Tailscale hostname `pi3b`,
  user `pi`, the dedicated identity file, and strict host-key checking.
- Give the runner user Docker access. Do not place personal SSH keys, private
  deployment keys, Tailscale authentication keys, or `.env` contents in the
  repository or GitHub Actions secrets.

The runner registration token is short-lived and is used only while installing
the service. Tailscale enrollment is also a one-time interactive operation.

## Image retention and recovery

AWS retains the deployed backend/web image IDs and one rollback pair. Cleanup
matches only `openlab-*` and `deploy-openlab-*` repositories; it never invokes a
broad Docker system or volume prune. If necessary, it may prune cache older than
24 hours from Docker's `default` builder, which is used for OpenLab. It never
prunes the separate `ruview-arm` builder or its volume. A build stops before
compilation if less than 2 GiB is free after this scoped cleanup.

The Pi records the previous image IDs before changing the Compose aliases. If
containers, image IDs, worker startup, or HTTP smoke tests fail, the activation
script restores the previous aliases and reports the workflow as failed. Named
volumes and database contents are preserved; database migrations are not
automatically reversed.
