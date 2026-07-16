from app.auth.service import seed_development_players
from app.clock import clock_from_iso
from app.config import Settings
from app.db.session import create_database_engine, create_schema, session_factory
from app.seasons import seed_development_season
from app.teams.service import seed_fixed_teams


def main() -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    create_schema(engine)
    with session_factory(engine)() as session:
        season = seed_development_season(session)
        players = seed_development_players(session, clock_from_iso(settings.dev_now)())
        team_count = len(seed_fixed_teams(session, season))
    print(
        f"Season {season.name}, {len(players)} development players, "
        f"and {team_count} teams are ready."
    )


if __name__ == "__main__":
    main()
