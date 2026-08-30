"""Installation contracts and bounded, secret-free readiness checks.

No Docker socket or privileged host operations are available to the web app.
The installer publishes a redacted status file and reads the validated update policy.
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from alembic.script import ScriptDirectory
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import Settings
from .models import Job, Lab, ProviderConfig, ServiceHeartbeat
from .system_settings import effective_kicad_cli


class ReadinessCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(max_length=100)
    required: bool
    status: Literal["pending", "pass", "warn", "fail"]
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")
    summary: str = Field(max_length=500)
    remediation: str | None = Field(default=None, max_length=500)


class ReadinessReport(BaseModel):
    overall: Literal["ready", "ready_with_warnings", "blocked"]
    version: str
    checked_at: datetime
    checks: list[ReadinessCheck]


class InstallationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    security_updates: bool = True
    weekday: int = Field(default=0, ge=0, le=6, description="Sunday=0; host local time")
    hour: int = Field(default=3, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class InstallerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    checked_at: datetime
    version: str = Field(max_length=100)
    checks: list[ReadinessCheck] = Field(max_length=30)
    tailscale: Literal["not_installed", "needs_authorization", "connected", "unavailable"] = (
        "not_installed"
    )
    update_status: Literal[
        "idle",
        "current",
        "available",
        "updating",
        "updated",
        "rolled_back",
        "failed",
        "manual_required",
    ] = "idle"


class InstallationOverview(BaseModel):
    managed: bool
    policy: InstallationPolicy
    status: InstallerStatus | None
    status_stale: bool


def normalize_public_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Enter a valid HTTP or HTTPS origin") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(ord(char) < 33 for char in value)
        or "\\" in value
        or len(value) > 600
    ):
        raise ValueError(
            "Use an HTTP or HTTPS origin without credentials, path, query, or fragment"
        )
    hostname = parsed.hostname.encode("idna").decode().lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    suffix = (
        f":{port}" if port and (parsed.scheme, port) not in {("http", 80), ("https", 443)} else ""
    )
    return f"{parsed.scheme}://{hostname}{suffix}"


class NetworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_url: str

    @field_validator("public_url")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        return normalize_public_url(value)


class NetworkOut(BaseModel):
    public_url: str | None
    source: Literal["settings", "environment", "unset"]
    verified: bool


class OnboardingOut(BaseModel):
    completed_at: datetime | None
    network: NetworkOut
    readiness: ReadinessReport


def network_out(lab: Lab, settings: Settings) -> NetworkOut:
    return NetworkOut(
        public_url=lab.public_url or settings.public_url,
        source="settings" if lab.public_url else "environment" if settings.public_url else "unset",
        verified=bool(lab.public_url and lab.public_url_verified_at),
    )


def provider_fingerprint(config: ProviderConfig) -> str:
    # Include ciphertext identity so changing a stored credential invalidates a prior test.
    return sha256(
        json.dumps(
            [config.base_url, config.model, config.embedding_model, config.secret_ciphertext]
        ).encode()
    ).hexdigest()


def read_control_json(directory: Path, filename: str) -> bytes:
    path = directory / filename
    if path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ValueError("Invalid installer control file")
    return path.read_bytes()


def installation_overview(settings: Settings) -> InstallationOverview:
    directory = settings.installer_control_dir
    policy = InstallationPolicy()
    status = None
    if directory:
        try:
            policy = InstallationPolicy.model_validate_json(
                read_control_json(directory / "policy", "policy.json")
            )
        except (OSError, ValueError, ValidationError):
            # Never turn malformed policy into permission to perform unattended updates.
            policy = InstallationPolicy(security_updates=False)
        try:
            status = InstallerStatus.model_validate_json(
                read_control_json(directory, "status.json")
            )
        except (OSError, ValueError, ValidationError):
            pass
    age = (
        (datetime.now(UTC) - status.checked_at).total_seconds()
        if status and status.checked_at.tzinfo
        else -1
    )
    return InstallationOverview(
        managed=bool(directory),
        policy=policy,
        status=status,
        status_stale=not (0 <= age <= 600),
    )


def save_installation_policy(settings: Settings, policy: InstallationPolicy) -> None:
    directory = settings.installer_control_dir
    directory = directory / "policy" if directory else None
    if not directory or not directory.is_dir():
        raise ValueError(
            "This deployment is not managed by openlabctl; configure updates on the host"
        )
    descriptor, name = tempfile.mkstemp(prefix=".policy-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(policy.model_dump(), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / "policy.json")
    finally:
        Path(name).unlink(missing_ok=True)


def summarize(checks: list[ReadinessCheck], version: str) -> ReadinessReport:
    blocked = any(check.required and check.status != "pass" for check in checks)
    warnings = any(check.status != "pass" for check in checks)
    return ReadinessReport(
        overall="blocked" if blocked else "ready_with_warnings" if warnings else "ready",
        version=version,
        checked_at=datetime.now(UTC),
        checks=checks,
    )


def application_readiness(db: Session, lab: Lab, settings: Settings) -> ReadinessReport:
    checks: list[ReadinessCheck] = []

    def check(
        identifier: str,
        label: str,
        ok: bool,
        code: str,
        summary: str,
        remediation: str,
        *,
        required: bool = True,
    ) -> None:
        checks.append(
            ReadinessCheck(
                id=identifier,
                label=label,
                required=required,
                status="pass" if ok else "fail" if required else "warn",
                code="OK" if ok else code,
                summary=summary,
                remediation=None if ok else remediation,
            )
        )

    db.execute(select(1))
    check(
        "database",
        "Database connection",
        True,
        "DATABASE_UNREACHABLE",
        "PostgreSQL answered a query.",
        "openlabctl doctor",
    )
    heads = set(ScriptDirectory(str(Path(__file__).resolve().parents[2] / "alembic")).get_heads())
    current = set(db.execute(text("SELECT version_num FROM alembic_version")).scalars())
    check(
        "migrations",
        "Database migrations",
        current == heads,
        "MIGRATIONS_PENDING",
        "Schema is current."
        if current == heads
        else "Database schema does not match this release.",
        "openlabctl repair migrations",
    )
    heartbeat = db.scalar(
        select(ServiceHeartbeat)
        .where(ServiceHeartbeat.service == "worker")
        .order_by(ServiceHeartbeat.last_seen_at.desc())
        .limit(1)
    )
    age = (datetime.now(UTC) - heartbeat.last_seen_at).total_seconds() if heartbeat else -1
    worker_ok = 0 <= age <= 60 and bool(heartbeat and heartbeat.version == settings.version)
    check(
        "worker",
        "Background worker",
        worker_ok,
        "WORKER_UNAVAILABLE",
        "A worker of this release reported within 60 seconds."
        if worker_ok
        else "No fresh heartbeat from a worker of this release.",
        "openlabctl repair worker",
    )
    storage_ok = False
    try:
        with tempfile.TemporaryFile(dir=settings.data_dir) as stream:
            stream.write(b"openlab-readiness")
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            storage_ok = stream.read() == b"openlab-readiness"
    except OSError:
        pass
    check(
        "storage",
        "Attachment storage",
        storage_ok,
        "STORAGE_NOT_WRITABLE",
        "Temporary storage read/write succeeded."
        if storage_ok
        else "Attachment storage cannot be read and written.",
        "openlabctl doctor",
    )
    secret_ok = (
        len(settings.secret_key) >= 32 and settings.secret_key != "development-only-change-me"
    )
    check(
        "session_secret",
        "Session secret",
        secret_ok,
        "SECRET_NOT_CONFIGURED",
        "Session secret is configured." if secret_ok else "A secure session secret is required.",
        "openlabctl repair secrets",
    )
    encryption_ok = False
    try:
        Fernet((settings.encryption_key or "").encode())
        encryption_ok = True
    except (ValueError, TypeError):
        pass
    check(
        "encryption",
        "Credential encryption",
        encryption_ok,
        "ENCRYPTION_NOT_CONFIGURED",
        "Credential encryption key is valid."
        if encryption_ok
        else "A valid encryption key is required.",
        "openlabctl repair secrets",
    )
    check(
        "owner",
        "Owner account",
        True,
        "OWNER_REQUIRED",
        "An authenticated owner is completing setup.",
        "Open /setup",
    )
    check(
        "public_url",
        "Browser and canonical URL",
        bool(lab.public_url and lab.public_url_verified_at),
        "URL_NOT_VERIFIED",
        "An authenticated browser verified the canonical origin."
        if lab.public_url_verified_at
        else "Save and verify the address from a browser visiting that address.",
        "Open the chosen URL, then save it under Network.",
    )
    provider = db.scalar(
        select(ProviderConfig)
        .where(ProviderConfig.lab_id == lab.id)
        .order_by(ProviderConfig.updated_at.desc())
        .limit(1)
    )
    provider_enabled = bool(provider and (provider.enabled or provider.embeddings_enabled))
    saved_test = (lab.integration_checks or {}).get("ai")
    tested = False
    if provider and isinstance(saved_test, dict):
        try:
            tested_at = datetime.fromisoformat(str(saved_test.get("checked_at", "")))
            tested = (
                saved_test.get("fingerprint") == provider_fingerprint(provider)
                and 0 <= (datetime.now(UTC) - tested_at).total_seconds() < 3600
            )
        except (ValueError, TypeError):
            pass
    checks.append(
        ReadinessCheck(
            id="ai",
            label="Optional AI endpoint",
            required=False,
            status="pass" if provider_enabled and tested else "warn",
            code="OK"
            if provider_enabled and tested
            else "AI_NOT_TESTED"
            if provider_enabled
            else "AI_DISABLED",
            summary="Model listing succeeded recently; generation capabilities are model-dependent."
            if provider_enabled and tested
            else "Configured; test the endpoint."
            if provider_enabled
            else "AI is disabled; manual inventory remains available.",
            remediation="Test the endpoint in the AI step."
            if provider_enabled and not tested
            else None,
        )
    )
    cli, _ = effective_kicad_cli(lab, settings)
    kicad_job = db.scalar(
        select(Job)
        .where(Job.lab_id == lab.id, Job.kind == "system.kicad_check")
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    kicad_ok = bool(
        cli
        and kicad_job
        and kicad_job.status == "completed"
        and kicad_job.result
        and kicad_job.result.get("status") == "available"
        and kicad_job.result.get("cli") == cli
    )
    check(
        "kicad",
        "Optional KiCad",
        kicad_ok,
        "KICAD_NOT_VERIFIED",
        "The worker detected the configured KiCad binary."
        if kicad_ok
        else "KiCad is optional and has not been verified for this configuration.",
        "Save and check KiCad in Settings.",
        required=False,
    )
    # Host checks are evidence from openlabctl, never obtained through a Docker socket.
    installer = installation_overview(settings)
    if installer.managed:
        if installer.status_stale:
            check(
                "host_status",
                "Installer diagnostics",
                False,
                "HOST_STATUS_STALE",
                "Fresh installer diagnostics are unavailable.",
                "openlabctl doctor --write-status",
            )
        elif installer.status:
            checks.extend(
                item.model_copy(update={"id": f"host_{item.id}"})
                for item in installer.status.checks
            )
    return summarize(checks, settings.version)
