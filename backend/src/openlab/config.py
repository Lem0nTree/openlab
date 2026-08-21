from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "OpenLab"
    database_url: str
    data_dir: Path = Path("./data")
    secret_key: str = "development-only-change-me"
    setup_token: str | None = None
    encryption_key: str | None = None
    session_hours: int = 24 * 14
    upload_max_bytes: int = 25 * 1024 * 1024

    @property
    def bootstrap_token(self) -> str:
        return self.setup_token or token_urlsafe(24)


@lru_cache
def get_settings() -> Settings:
    return Settings()
