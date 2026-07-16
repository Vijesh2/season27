from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import LONDON
from app.db.models import Base, Season, Standing
from app.scoring import score_prediction
from app.seasons import seed_development_season
from app.standings.service import (
    InvalidStandings,
    StandingInput,
    create_snapshot,
    get_latest_snapshot,
    seed_development_snapshot,
)
from app.teams.service import get_season_teams, seed_fixed_teams


def database() -> tuple[Session, Season, list[int]]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    season = seed_development_season(session)
    team_ids = [item.team_id for item in seed_fixed_teams(session, season)]
    return session, season, team_ids


def valid_rows(team_ids: list[int]) -> list[StandingInput]:
    return [
        StandingInput(team_id=team_id, position=position)
        for position, team_id in enumerate(team_ids, start=1)
    ]


@pytest.mark.parametrize("kind", ["missing", "duplicate_team", "duplicate_position"])
def test_snapshot_validation_requires_exact_team_and_position_permutation(kind: str) -> None:
    session, season, team_ids = database()
    rows = valid_rows(team_ids)
    if kind == "missing":
        rows = rows[:-1]
    elif kind == "duplicate_team":
        rows[-1] = StandingInput(team_id=team_ids[0], position=20)
    else:
        rows[-1] = StandingInput(team_id=team_ids[-1], position=1)
    with pytest.raises(InvalidStandings):
        create_snapshot(
            session,
            season.id,
            rows,
            datetime(2026, 9, 1, tzinfo=LONDON),
            source="test",
        )


def test_snapshots_are_versioned_and_scores_are_historically_reproducible() -> None:
    session, season, team_ids = database()
    prediction = {team_id: position for position, team_id in enumerate(team_ids, start=1)}
    first = create_snapshot(
        session,
        season.id,
        valid_rows(team_ids),
        datetime(2026, 9, 1, tzinfo=LONDON),
        source="test",
    )
    reversed_rows = valid_rows(list(reversed(team_ids)))
    second = create_snapshot(
        session,
        season.id,
        reversed_rows,
        datetime(2026, 9, 2, tzinfo=LONDON),
        source="test",
        is_final=True,
    )
    assert (first.version, second.version) == (1, 2)
    latest = get_latest_snapshot(session, season.id)
    assert latest is not None and latest.id == second.id
    first_actual = {row.team_id: row.position for row in first.rows}
    second_actual = {row.team_id: row.position for row in second.rows}
    assert score_prediction(1, prediction, first_actual).total == 0
    assert score_prediction(1, prediction, second_actual).total == 200
    assert first.is_final is False and second.is_final is True


def test_database_constraints_protect_snapshot_rows() -> None:
    session, season, team_ids = database()
    snapshot = create_snapshot(
        session,
        season.id,
        valid_rows(team_ids),
        datetime(2026, 9, 1, tzinfo=LONDON),
        source="test",
    )
    session.add(Standing(snapshot_id=snapshot.id, team_id=team_ids[0], position=20))
    with pytest.raises(IntegrityError):
        session.commit()


def test_development_snapshot_is_deterministic_and_idempotent() -> None:
    session, season, _ = database()
    now = datetime(2026, 9, 1, tzinfo=LONDON)
    first = seed_development_snapshot(session, season.id, now)
    second = seed_development_snapshot(session, season.id, now)
    assert first.id == second.id
    assert len(first.rows) == 20
    assert {row.team_id for row in first.rows} == {
        item.team_id for item in get_season_teams(session, season.id)
    }
