from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Base, SeasonTeam, Team
from app.seasons import seed_development_season
from app.teams.service import (
    FIXED_2026_27_TEAMS,
    get_season_teams,
    seed_fixed_teams,
    validate_fixed_teams,
)


def database() -> tuple[Session, object]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    return session, seed_development_season(session)


def test_fixed_design_list_has_twenty_unique_teams() -> None:
    validate_fixed_teams(FIXED_2026_27_TEAMS)
    assert len(FIXED_2026_27_TEAMS) == 20


def test_validation_rejects_wrong_count_and_duplicates() -> None:
    with pytest.raises(ValueError, match="exactly 20"):
        validate_fixed_teams(list(FIXED_2026_27_TEAMS[:-1]))
    duplicate = list(FIXED_2026_27_TEAMS)
    duplicate[-1] = replace(
        duplicate[-1],
        name=duplicate[0].name,
        slug=duplicate[0].slug,
        source_identity=duplicate[0].source_identity,
    )
    with pytest.raises(ValueError) as error:
        validate_fixed_teams(duplicate)
    assert "duplicate team names" in str(error.value)
    assert "duplicate slugs" in str(error.value)
    assert "duplicate source identities" in str(error.value)


def test_fixed_teams_seed_once_and_persist() -> None:
    session, season = database()
    first = seed_fixed_teams(session, season)  # type: ignore[arg-type]
    second = seed_fixed_teams(session, season)  # type: ignore[arg-type]
    assert len(first) == len(second) == 20
    assert len(session.scalars(select(Team)).all()) == 20
    assert len(session.scalars(select(SeasonTeam)).all()) == 20
    assert [item.team.name for item in get_season_teams(session, season.id)] == [
        item.name for item in FIXED_2026_27_TEAMS
    ]
