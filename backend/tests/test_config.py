from openlab.config import Settings


def test_prefixed_openlab_settings_are_loaded(monkeypatch) -> None:
    monkeypatch.setenv("OPENLAB_DATA_DIR", "/var/lib/openlab")
    monkeypatch.setenv("OPENLAB_SECRET_KEY", "session-secret")
    monkeypatch.setenv("OPENLAB_SETUP_TOKEN", "setup-token")
    monkeypatch.setenv("OPENLAB_ENCRYPTION_KEY", "encryption-key")

    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://openlab:openlab@localhost:5432/openlab",
    )

    assert str(settings.data_dir).replace("\\", "/") == "/var/lib/openlab"
    assert settings.secret_key == "session-secret"
    assert settings.setup_token == "setup-token"
    assert settings.encryption_key == "encryption-key"
