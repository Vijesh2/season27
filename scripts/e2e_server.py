import os
from datetime import datetime
from pathlib import Path

import uvicorn
from pydantic import SecretStr

from app.clock import LONDON, MutableClock
from app.config import Settings
from app.main import create_app

TOKEN = "local-browser-test-token"
database_url = os.environ.get("SEASON27_E2E_DATABASE_URL", "sqlite:///e2e.db")
if database_url == "sqlite:///e2e.db":
    Path("e2e.db").unlink(missing_ok=True)
clock = MutableClock(datetime(2026, 8, 10, 12, tzinfo=LONDON))
settings = Settings(
    database_url=database_url,
    environment="test",
    secret_key="isolated-browser-test-secret",
    test_control_token=SecretStr(TOKEN),
)
app = create_app(settings=settings, clock=clock)


def main() -> None:
    uvicorn.run("scripts.e2e_server:app", host="127.0.0.1", port=5010)


if __name__ == "__main__":
    main()
