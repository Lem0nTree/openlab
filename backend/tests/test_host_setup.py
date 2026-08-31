import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from openlab.config import Settings
from openlab.host_setup import HostSetupInput, host_setup_status, queue_host_setup
from openlab.main import app, get_db, get_mcp_integration
from openlab.models import Lab, User
from openlab.security import current_user


def setup_host(tmp_path: Path) -> Settings:
    (tmp_path / "policy").mkdir()
    (tmp_path / "setup-status.json").write_text(json.dumps({
        "checked_at": datetime.now(UTC).isoformat(), "tailscale": "connected", "kicad_supported": True,
    }))
    return Settings(_env_file=None, database_url="postgresql+psycopg://test:test@localhost/test", installer_control_dir=tmp_path)


def test_request_contains_only_fixed_action_and_rejects_second_request(tmp_path: Path) -> None:
    settings = setup_host(tmp_path)
    result = queue_host_setup(settings, HostSetupInput(action="kicad"))
    request = json.loads((tmp_path / "policy/setup-request.json").read_text())
    assert request == {"id": result.id, "action": "kicad", "requested_at": result.model_dump(mode="json")["requested_at"]}
    with pytest.raises(ValueError, match="already queued"):
        queue_host_setup(settings, HostSetupInput(action="https"))
    assert json.loads((tmp_path / "policy/setup-request.json").read_text()) == request


def test_stale_host_is_unknown_not_not_installed(tmp_path: Path) -> None:
    settings = setup_host(tmp_path)
    assert host_setup_status(settings).tailscale == "connected"
    (tmp_path / "setup-status.json").write_text(json.dumps({
        "checked_at": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(), "tailscale": "not_installed",
    }))
    assert not host_setup_status(settings).available
    assert host_setup_status(settings).tailscale == "unavailable"
    with pytest.raises(ValueError, match="unavailable"):
        queue_host_setup(settings, HostSetupInput(action="tailscale"))


@pytest.mark.parametrize("payload", [{"action": "sh"}, {"action": "kicad", "image": "evil:latest"}, {"action": "https", "url": "http://evil"}])
def test_setup_rejects_arbitrary_authority(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        HostSetupInput.model_validate(payload)


def test_setup_requires_owner_session_and_csrf() -> None:
    client = TestClient(app)
    assert client.get("/api/v1/settings/host-setup").status_code == 401
    assert client.post("/api/v1/settings/host-setup", json={"action": "kicad"}).status_code in (401, 403)


def test_owner_request_is_queued_and_audited_and_requires_csrf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = setup_host(tmp_path)
    monkeypatch.setattr("openlab.main.settings", settings)
    monkeypatch.setattr("openlab.main.lab_for_user", lambda *_: "lab")
    monkeypatch.setattr("openlab.services.lab_for_user", lambda *_: "lab")
    db = MagicMock()
    app.dependency_overrides[current_user] = lambda: User(id="owner", is_owner=True)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        assert client.post("/api/v1/settings/host-setup", json={"action": "kicad"}).status_code == 403
        client.cookies.set("openlab_csrf", "test-csrf")
        response = client.post("/api/v1/settings/host-setup", json={"action": "kicad"}, headers={"X-CSRF-Token": "test-csrf"})
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "queued"
        assert db.add.call_args.args[0].details["setup_action"] == "kicad"
        db.commit.assert_called_once()
        app.dependency_overrides[current_user] = lambda: User(id="member", is_owner=False)
        assert client.get("/api/v1/settings/host-setup").status_code == 403
        assert client.post("/api/v1/settings/host-setup", json={"action": "https"}, headers={"X-CSRF-Token": "test-csrf"}).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_older_release_cannot_queue_unsupported_kicad(tmp_path: Path) -> None:
    settings = setup_host(tmp_path)
    path = tmp_path / "setup-status.json"
    status = json.loads(path.read_text())
    status["kicad_supported"] = False
    path.write_text(json.dumps(status))
    with pytest.raises(ValueError, match="no signed KiCad"):
        queue_host_setup(settings, HostSetupInput(action="kicad"))


def test_mcp_does_not_claim_unverified_https_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openlab.main.lab_for_user", lambda *_: "lab")
    db = MagicMock()
    lab = Lab(id="lab", public_url="https://lab.example.ts.net", mcp_enabled=True)
    db.get.return_value = lab
    db.scalars.return_value.all.return_value = []
    assert get_mcp_integration(User(id="owner"), db)["direct_http_ready"] is False
    lab.public_url_verified_at = datetime.now(UTC)
    assert get_mcp_integration(User(id="owner"), db)["direct_http_ready"] is True
