import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import Settings
from scripts.seed_development import main as seed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Destructively reset the isolated staging database"
    )
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.environment != "staging":
        raise SystemExit("Refusing reset: SEASON27_ENVIRONMENT must be staging")
    if args.confirm != "RESET-STAGING":
        raise SystemExit("Refusing reset: pass --confirm RESET-STAGING")
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise SystemExit("Automated staging reset supports SQLite only")
    database = Path(settings.database_url.removeprefix(prefix)).resolve()
    database.unlink(missing_ok=True)
    command.upgrade(Config("alembic.ini"), "head")
    seed()
    print(f"Staging database reset at {database}")


if __name__ == "__main__":
    main()
