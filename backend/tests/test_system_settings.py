from unittest.mock import Mock

import pytest

from openlab.config import Settings
from openlab.main import deployment_environment
from openlab.models import Lab
from openlab.system_settings import check_kicad_cli, effective_kicad_cli, normalize_kicad_cli


def config(**values: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://openlab:openlab@localhost:5432/openlab",
        **values,
    )


def test_kicad_setting_overrides_environment_and_can_fall_back() -> None:
    lab = Lab(name="Lab", units="metric", kicad_cli="/custom/kicad-cli")
    assert effective_kicad_cli(lab, config(kicad_cli="/env/kicad-cli")) == (
        "/custom/kicad-cli",
        "settings",
    )
    lab.kicad_cli = None
    assert effective_kicad_cli(lab, config(kicad_cli="/env/kicad-cli")) == (
        "/env/kicad-cli",
        "environment",
    )
    assert effective_kicad_cli(lab, config(kicad_cli=None)) == (None, "unset")


def test_kicad_command_is_narrowly_validated() -> None:
    assert normalize_kicad_cli(" kicad-cli ") == "kicad-cli"
    assert normalize_kicad_cli("/usr/bin/kicad-cli") == "/usr/bin/kicad-cli"
    assert normalize_kicad_cli("") is None
    with pytest.raises(ValueError, match="must end with"):
        normalize_kicad_cli("/bin/sh")


def test_kicad_check_reports_worker_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openlab.system_settings.shutil.which", lambda _: "/usr/bin/kicad-cli")
    completed = Mock(returncode=0, stdout="8.0.2\n", stderr="")
    run = Mock(return_value=completed)
    monkeypatch.setattr("openlab.system_settings.subprocess.run", run)

    assert check_kicad_cli("kicad-cli") == {"status": "available", "version": "8.0.2"}
    run.assert_called_once_with(
        ["/usr/bin/kicad-cli", "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )


def test_kicad_check_explains_missing_worker_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openlab.system_settings.shutil.which", lambda _: None)
    monkeypatch.setattr("openlab.system_settings.Path.is_file", lambda _: False)
    result = check_kicad_cli("kicad-cli")
    assert result["status"] == "unavailable"
    assert "worker container" in str(result["error"])


def test_deployment_catalog_never_returns_secret_values() -> None:
    secret_entries = [item for item in deployment_environment() if item["secret"]]
    assert secret_entries
    assert all(item["value"] is None for item in secret_entries)
