from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    @property
    def bootstrap_token(self) -> str:
        return self.setup_token or token_urlsafe(24)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from configured settings sources
