import os

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_rejects_default_secret_and_time_override(tmp_path: object) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(environment="production", database_url="sqlite:////tmp/prod.db")
    with pytest.raises(ValidationError, match="DEV_NOW"):
        Settings(
            environment="production",
            database_url="sqlite:////tmp/prod.db",
            secret_key="strong-secret",
            dev_now="2026-08-01T00:00:00+01:00",
            bootstrap_admin_name="Admin",
            bootstrap_admin_code="T001",
        )


def test_railway_volume_supplies_default_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
    settings = Settings()
    assert settings.database_url == "sqlite:////data/season27.db"
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH")
    assert "RAILWAY_VOLUME_MOUNT_PATH" not in os.environ
