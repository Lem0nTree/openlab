from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from openlab.config import Settings
from openlab.installation import (
    InstallationPolicy,
    NetworkInput,
    ReadinessCheck,
    application_readiness,
    installation_overview,
    normalize_public_url,
    save_installation_policy,
    summarize,
)
from openlab.main import app, complete_onboarding, save_network_settings, setup
from openlab.models import Lab, ServiceHeartbeat, User
from openlab.schemas import SetupRequest


def config(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test",
                    data_dir=tmp_path, secret_key="x" * 48,
                    encryption_key=Fernet.generate_key().decode(), **kwargs)


@pytest.mark.parametrize("value", ["https://host/path", "https://user:secret@host", "file:///etc/passwd",
    "http://host?token=secret", "http://host/#token", "http://host:99999", "http://host\\evil", "//host", "http://ho st"])
def test_network_rejects_unsafe_or_non_origin_urls(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_public_url(value)


def test_network_normalizes_origin() -> None:
    assert normalize_public_url(" https://LAB.local:443/ ") == "https://lab.local"
    assert normalize_public_url("http://[::1]:3000") == "http://[::1]:3000"


def test_policy_rejects_shell_fields_and_bad_schedule() -> None:
    for payload in ({"command": "sh"}, {"weekday": 7}, {"hour": 24}, {"minute": -1}):
        with pytest.raises(ValidationError):
            InstallationPolicy.model_validate(payload)


def test_policy_round_trip_and_fail_closed(tmp_path: Path) -> None:
    settings = config(tmp_path, installer_control_dir=tmp_path)
    (tmp_path / "policy").mkdir()
    assert installation_overview(settings).policy.security_updates is False
    save_installation_policy(settings, InstallationPolicy(hour=5))
    assert installation_overview(settings).policy.hour == 5
    (tmp_path / "policy" / "policy.json").write_text('{"security_updates":true,"command":"bad"}')
    assert installation_overview(settings).policy.security_updates is False
    assert installation_overview(settings).status_stale is True


def test_empty_compose_control_setting_is_unmanaged(tmp_path: Path) -> None:
    settings = config(tmp_path, installer_control_dir="")
    assert settings.installer_control_dir is None
    assert installation_overview(settings).managed is False


def test_required_pending_blocks_but_optional_failure_does_not() -> None:
    required = ReadinessCheck(id="database", label="Database", required=True,
        status="pending", code="PENDING", summary="Waiting")
    optional = ReadinessCheck(id="ai", label="AI", required=False,
        status="fail", code="AI_DISABLED", summary="Skipped")
    assert summarize([required, optional], "test").overall == "blocked"
    assert summarize([required.model_copy(update={"status": "pass"}), optional], "test").overall == "ready_with_warnings"


def test_readiness_detects_stale_worker_and_never_returns_credentials(tmp_path: Path) -> None:
    db = MagicMock()
    db.execute.return_value.scalars.return_value = ["0010_installation_onboarding"]
    db.scalar.side_effect = [ServiceHeartbeat(instance_id="worker", service="worker", version="development",
        last_seen_at=datetime.now(UTC) - timedelta(minutes=2)), None, None]
    lab = Lab(id="lab", name="Lab", public_url="http://lab.local", public_url_verified_at=datetime.now(UTC))
    settings = config(tmp_path)
    result = application_readiness(db, lab, settings)
    assert result.overall == "blocked"
    assert next(check for check in result.checks if check.id == "worker").code == "WORKER_UNAVAILABLE"
    assert settings.secret_key not in result.model_dump_json()
    assert settings.encryption_key not in result.model_dump_json()


def test_fresh_matching_worker_and_optional_skips_are_ready(tmp_path: Path) -> None:
    db = MagicMock()
    db.execute.return_value.scalars.return_value = ["0011_product_mcp"]
    db.scalar.side_effect = [ServiceHeartbeat(instance_id="worker", service="worker", version="development",
        last_seen_at=datetime.now(UTC)), None, None]
    lab = Lab(id="lab", name="Lab", public_url="http://lab.local", public_url_verified_at=datetime.now(UTC))
    assert application_readiness(db, lab, config(tmp_path)).overall == "ready_with_warnings"


def test_network_verification_requires_matching_authenticated_browser_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    lab = Lab(id="lab", name="Lab")
    monkeypatch.setattr("openlab.main.owner_lab", lambda *_: lab)
    monkeypatch.setattr("openlab.main.audit", lambda *_args, **_kwargs: None)
    db = MagicMock()
    user = User(id="owner", is_owner=True)
    request = Request({"type": "http", "headers": [(b"origin", b"http://lab.local:3000")]})
    result = save_network_settings(NetworkInput(public_url="http://lab.local:3000"), request, user, db)
    assert result.verified is True
    result = save_network_settings(NetworkInput(public_url="http://elsewhere.local:3000"), request, user, db)
    assert result.verified is False


def test_setup_serializes_first_owner_and_creates_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openlab.main.bootstrap_token", "token")
    monkeypatch.setattr("openlab.main.create_session", lambda *_: ("session", "csrf"))
    db = MagicMock()
    db.scalar.return_value = 0
    response = Response()
    setup(SetupRequest(token="token", lab_name="Lab", email="owner@example.test", display_name="Owner",
                       password="long-enough-test-password"), response, db)
    assert "pg_advisory_xact_lock" in str(db.execute.call_args.args[0])
    assert "openlab_session=session" in str(response.headers.raw)
    assert "httponly" in str(response.headers.raw).lower()
    db.scalar.return_value = 1
    with pytest.raises(HTTPException) as raised:
        setup(SetupRequest(token="token", lab_name="Lab", email="owner@example.test", display_name="Owner",
                           password="long-enough-test-password"), Response(), db)
    assert raised.value.status_code == 409


def test_completion_cannot_ignore_required_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    lab = Lab(id="lab", name="Lab")
    monkeypatch.setattr("openlab.main.owner_lab", lambda *_: lab)
    report = summarize([ReadinessCheck(id="worker", label="Worker", required=True,
        status="fail", code="WORKER_UNAVAILABLE", summary="Unavailable")], "test")
    monkeypatch.setattr("openlab.main.application_readiness", lambda *_: report)
    with pytest.raises(HTTPException) as raised:
        complete_onboarding(User(id="owner", is_owner=True), MagicMock())
    assert raised.value.status_code == 409
    assert lab.onboarding_completed_at is None


def test_installation_apis_require_authentication_and_hide_invalid_secrets() -> None:
    client = TestClient(app)
    for route in ("/readiness", "/onboarding", "/settings/network", "/settings/installation"):
        assert client.get(f"/api/v1{route}").status_code == 401
    # Required-fields errors must not echo the raw request's password or setup token.
    response = client.post("/api/v1/setup", json={"password": "secr3t", "token": "private-token"})
    assert response.status_code == 422
    assert "secr3t" not in response.text
    assert "private-token" not in response.text
