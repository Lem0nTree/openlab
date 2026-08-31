"""Owner-only requests for fixed installer operations; never a host shell API."""

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .installation import read_control_json

SetupAction = Literal["refresh", "kicad", "tailscale", "https"]
TailscaleState = Literal["not_installed", "needs_authorization", "connected", "unavailable"]
_queue_lock = threading.Lock()


class HostSetupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: SetupAction


class HostSetupOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    action: SetupAction
    requested_at: datetime
    status: Literal["queued", "running", "completed", "failed"]
    message: str = Field(max_length=1000)
    url: str | None = Field(default=None, pattern=r"^https://[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$")


class HostSetupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool = False
    checked_at: datetime | None = None
    tailscale: TailscaleState = "unavailable"
    kicad_supported: bool = False
    operation: HostSetupOperation | None = None


def host_setup_status(settings: Settings) -> HostSetupOut:
    directory = settings.installer_control_dir
    if not directory:
        return HostSetupOut()
    try:
        status = HostSetupOut.model_validate_json(read_control_json(directory, "setup-status.json"))
        age = (datetime.now(UTC) - status.checked_at).total_seconds() if status.checked_at else -1
        # Long jobs publish phase changes. Do not submit another during a running job.
        status.available = 0 <= age <= (1500 if status.operation and status.operation.status == "running" else 90)
        if not status.available:
            status.tailscale = "unavailable"
        return status
    except (OSError, ValueError, TypeError):
        return HostSetupOut()


def queue_host_setup(settings: Settings, payload: HostSetupInput) -> HostSetupOperation:
    with _queue_lock:
        status = host_setup_status(settings)
        if not status.available or not settings.installer_control_dir:
            raise ValueError("Host setup service is unavailable. Update using the signed installer on the host first.")
        if status.operation and status.operation.status == "running":
            raise ValueError("A host setup operation is already running. Wait for it to finish.")
        if payload.action == "kicad" and not status.kicad_supported:
            raise ValueError("This release has no signed KiCad worker. Upgrade with the current installer first.")
        directory = settings.installer_control_dir / "policy"
        destination = directory / "setup-request.json"
        # Never replace another owner's queued operation. Atomic publication is
        # coordinated with all server processes using exclusive hard-link creation.
        operation = HostSetupOperation(id=uuid4().hex, action=payload.action,
            requested_at=datetime.now(UTC), status="queued", message="Waiting for the host installer")
        fd, name = tempfile.mkstemp(prefix=".setup-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(operation.model_dump(mode="json", include={"id", "action", "requested_at"}), stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(name, destination)
        except FileExistsError as exc:
            raise ValueError("A host setup request is already queued. Wait for it to finish.") from exc
        finally:
            Path(name).unlink(missing_ok=True)
        return operation
