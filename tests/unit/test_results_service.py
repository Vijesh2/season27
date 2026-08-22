from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import FootballResult
from app.db.session import create_database_engine, create_schema
from app.results.service import import_results
from app.results.source import ExternalResult, ResultsSourceError
from app.seasons import seed_development_season
from app.teams.service import seed_fixed_teams


def result() -> ExternalResult:
    return ExternalResult(
        event_id="s-one",
        competition="Premier League",
        home_identity="arsenal",
        home_name="Arsenal",
        away_identity="chelsea",
        away_name="Chelsea",
        home_score=3,
        away_score=1,
        scheduled_at=datetime(2026, 8, 31, 15, 30, tzinfo=UTC),
        completed_at=datetime(2026, 8, 31, 17, 25, tzinfo=UTC),
        status="PostEvent",
        source_url="https://example.test/2026-08-31",
        source_metadata={"status_comment": {"value": "FT"}},
    )


def database(path: Path) -> tuple[Session, int]:
    engine = create_database_engine(f"sqlite:///{path}")
    create_schema(engine)
    session = Session(engine)
    season = seed_development_season(session)
    seed_fixed_teams(session, season)
    return session, season.id


def test_import_inserts_then_updates_without_duplicates(tmp_path: Path) -> None:
    session, season_id = database(tmp_path / "results.db")
    first_checked = datetime(2026, 9, 1, 6, tzinfo=UTC)
    second_checked = datetime(2026, 9, 1, 7, tzinfo=UTC)
    with session:
        first = import_results(session, season_id, (result(),), first_checked)
        assert (first.inserted, first.updated) == (1, 0)
        changed = replace(result(), home_score=4)
        second = import_results(session, season_id, (changed,), second_checked)
        assert (second.inserted, second.updated) == (0, 1)
        assert session.scalar(select(func.count(FootballResult.id))) == 1
        stored = session.scalar(select(FootballResult))
        assert stored is not None
        assert stored.home_score == 4
        assert stored.first_seen_at == first_checked.replace(tzinfo=None)
        assert stored.last_checked_at == second_checked.replace(tzinfo=None)


def test_import_rejects_unknown_season_team_without_partial_write(tmp_path: Path) -> None:
    session, season_id = database(tmp_path / "unknown.db")
    with session:
        unknown = replace(result(), home_identity="unknown", home_name="Unknown Athletic")
        with pytest.raises(ResultsSourceError, match="not in this season"):
            import_results(
                session,
                season_id,
                (result(), unknown),
                datetime(2026, 9, 1, 6, tzinfo=UTC),
            )
        assert session.scalar(select(func.count(FootballResult.id))) == 0
