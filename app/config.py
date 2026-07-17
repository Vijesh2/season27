import os
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEASON27_", env_file=".env")

    database_url: str = "sqlite:///season27.db"
    environment: str = "development"
    dev_now: str | None = None
    secret_key: str = "development-only-change-me"
    bootstrap_admin_name: str | None = None
    bootstrap_admin_code: SecretStr | None = None
    test_control_token: SecretStr | None = None
    host: str = "0.0.0.0"
    port: int = Field(default=5001, validation_alias=AliasChoices("SEASON27_PORT", "PORT"))
    session_days: int = 300
    login_attempt_limit: int = 5
    login_lock_minutes: int = 15
    standings_url: str = "https://www.bbc.co.uk/sport/football/premier-league/table"
    standings_cache_minutes: int = 15
    standings_stale_minutes: int = 30
    standings_refresh_throttle_seconds: int = 60
    standings_connect_timeout_seconds: float = 3.0
    standings_read_timeout_seconds: float = 8.0
    static_dir: Path = Path(__file__).parent / "static"

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production"

    @field_validator("dev_now")
    @classmethod
    def development_time_only(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def production_safety(self) -> "Settings":
        if self.database_url == "sqlite:///season27.db":
            volume = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
            if volume:
                self.database_url = f"sqlite:///{Path(volume) / 'season27.db'}"
        if self.environment == "production":
            if self.secret_key == "development-only-change-me":
                raise ValueError("SEASON27_SECRET_KEY must be changed in production")
            if self.dev_now is not None:
                raise ValueError("SEASON27_DEV_NOW is forbidden in production")
            if self.database_url == "sqlite:///season27.db":
                raise ValueError(
                    "production requires SEASON27_DATABASE_URL or RAILWAY_VOLUME_MOUNT_PATH"
                )
            if not self.bootstrap_admin_name or self.bootstrap_admin_code is None:
                raise ValueError(
                    "production requires bootstrap admin name and code environment variables"
                )
        return self
