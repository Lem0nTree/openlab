from datetime import UTC, datetime
from unittest.mock import MagicMock

from cryptography.fernet import Fernet

from openlab.config import Settings
from openlab.models import TelemetryState
from openlab.telemetry import activity_payload, ensure_state, production_reporting


def telemetry_settings(version: str = "v1.2.0") -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        secret_key="x" * 48,
        encryption_key=Fernet.generate_key().decode(),
        version=version,
    )


def test_development_builds_never_report_to_the_production_receiver() -> None:
    assert not production_reporting(telemetry_settings("development"))
    assert production_reporting(telemetry_settings())


def test_installation_identity_is_created_once_and_credential_is_not_plaintext() -> None:
    db = MagicMock()
    db.get.side_effect = [None]
    state = ensure_state(db, telemetry_settings())
    assert state.id == "installation"
    assert state.installation_id not in state.credential_ciphertext
    assert db.add.called


def test_activity_payload_contains_only_daily_aggregates() -> None:
    db = MagicMock()
    db.scalar.side_effect = [3, 18, 5, 1]
    state = TelemetryState(id="installation", installation_id="stable-installation", credential_ciphertext="cipher")
    payload = activity_payload(db, state, datetime(2026, 8, 31, 19, tzinfo=UTC), telemetry_settings())
    assert payload == {
        "schema_version": 1,
        "installation_id": "stable-installation",
        "app_version": "v1.2.0",
        "platform": payload["platform"],
        "activity_day": "2026-08-31",
        "inbox_processed": 3,
        "components_confirmed": 18,
        "things_created": 5,
        "projects_created": 1,
        "email_intake_enabled": False,
    }
