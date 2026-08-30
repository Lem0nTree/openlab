from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = "OpenLab"
    database_url: str
    data_dir: Path = Field(
        default=Path("./data"),
        validation_alias=AliasChoices("OPENLAB_DATA_DIR", "DATA_DIR"),
    )
    secret_key: str = Field(
        default="development-only-change-me",
        validation_alias=AliasChoices("OPENLAB_SECRET_KEY", "SECRET_KEY"),
    )
    setup_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENLAB_SETUP_TOKEN", "SETUP_TOKEN"),
    )
    encryption_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENLAB_ENCRYPTION_KEY", "ENCRYPTION_KEY"),
    )
    public_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENLAB_PUBLIC_URL", "PUBLIC_URL"),
    )
    session_hours: int = 24 * 14
    upload_max_bytes: int = 25 * 1024 * 1024
    kicad_cli: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENLAB_KICAD_CLI", "KICAD_CLI"),
    )
    version: str = Field(default="development", validation_alias="OPENLAB_VERSION")
    installer_control_dir: Path | None = Field(
        default=None, validation_alias="OPENLAB_INSTALLER_CONTROL_DIR"
    )

    @field_validator("installer_control_dir", mode="before")
    @classmethod
    def empty_control_directory_is_unmanaged(cls, value: object) -> object:
        # Compose passes optional variables as an empty string. Path("") would
        # become the working directory and incorrectly enable managed readiness.
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def bootstrap_token(self) -> str:
        return self.setup_token or token_urlsafe(24)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from configured settings sources
