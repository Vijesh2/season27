from app.auth.service import seed_development_players
from app.clock import clock_from_iso
from app.config import Settings
from app.db.session import create_database_engine, create_schema, session_factory
from app.seasons import seed_development_season
from app.teams.service import OFFICIAL_2026_27_TEAMS, get_roster, import_roster


def main() -> None:
    settings = Settings()
    engine = create_database_engine(settings.database_url)
    create_schema(engine)
    with session_factory(engine)() as session:
        season = seed_development_season(session)
        players = seed_development_players(session, clock_from_iso(settings.dev_now)())
        if not get_roster(session, season.id):
            import_roster(
                session,
                season,
                OFFICIAL_2026_27_TEAMS,
                clock_from_iso(settings.dev_now)(),
            )
        roster_count = len(get_roster(session, season.id))
    print(
        f"Season {season.name}, {len(players)} development players, "
        f"and {roster_count} teams are ready."
    )


if __name__ == "__main__":
    main()
