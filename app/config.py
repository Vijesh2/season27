from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEASON27_", env_file=".env")

    database_url: str = "sqlite:///season27.db"
    environment: str = "development"
    dev_now: str | None = None
    secret_key: str = "development-only-change-me"
    session_days: int = 300
    login_attempt_limit: int = 5
    login_lock_minutes: int = 15
    static_dir: Path = Path(__file__).parent / "static"

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production"

    @field_validator("dev_now")
    @classmethod
    def development_time_only(cls, value: str | None) -> str | None:
        return value or None
