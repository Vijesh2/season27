import logging

import uvicorn
from alembic import command
from alembic.config import Config

from app.config import Settings
from app.runtime import configure_logging


def main() -> None:
    configure_logging()
    settings = Settings()
    logging.getLogger(__name__).info("Applying database migrations")
    command.upgrade(Config("alembic.ini"), "head")
    logging.getLogger(__name__).info("Starting Season27")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        log_config=None,
    )


if __name__ == "__main__":
    main()
