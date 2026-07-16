from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.service import seed_development_players
from app.clock import LONDON
from app.db.models import Base, Prediction, PredictionSnapshot, PredictionStatus
from app.predictions.service import (
    InvalidPrediction,
    editing_is_open,
    ensure_draft,
    has_unsubmitted_changes,
    move_team,
    process_deadline,
    save_draft,
    submit_prediction,
)
from app.seasons import london, seed_development_season
from app.teams.service import seed_fixed_teams


def database() -> tuple[Session, object, int]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    season = seed_development_season(session)
    seed_fixed_teams(session, season)
    player = seed_development_players(session, now)[1]
    return session, season, player.id


def test_editing_boundaries() -> None:
    session, season, _ = database()
    assert not editing_is_open(season, datetime(2026, 7, 31, 23, 59, 59, tzinfo=LONDON))  # type: ignore[arg-type]
    assert editing_is_open(season, datetime(2026, 8, 1, tzinfo=LONDON))  # type: ignore[arg-type]
    assert editing_is_open(season, datetime(2026, 8, 20, 23, 59, 59, tzinfo=LONDON))  # type: ignore[arg-type]
    assert not editing_is_open(season, datetime(2026, 8, 21, tzinfo=LONDON))  # type: ignore[arg-type]
    session.close()


def test_ensure_draft_is_idempotent_and_move_respects_boundaries() -> None:
    session, season, player_id = database()
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    first = ensure_draft(session, player_id, season, now)  # type: ignore[arg-type]
    assert len(first) == 20
    assert len(ensure_draft(session, player_id, season, now)) == 20  # type: ignore[arg-type]
    unchanged = move_team(session, player_id, season, first[0].team_id, "up", now)  # type: ignore[arg-type]
    assert unchanged[0].team_id == first[0].team_id
    moved = move_team(session, player_id, season, first[0].team_id, "down", now)  # type: ignore[arg-type]
    assert moved[1].team_id == first[0].team_id


@pytest.mark.parametrize("order_kind", ["missing", "duplicate", "unknown"])
def test_invalid_orders_are_rejected_without_changing_draft(order_kind: str) -> None:
    session, season, player_id = database()
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    original = ensure_draft(session, player_id, season, now)  # type: ignore[arg-type]
    ids = [item.team_id for item in original]
    if order_kind == "missing":
        invalid = ids[:-1]
    elif order_kind == "duplicate":
        invalid = [*ids[:-1], ids[0]]
    else:
        invalid = [*ids[:-1], 99999]
    with pytest.raises(InvalidPrediction):
        save_draft(session, player_id, season, invalid, now)  # type: ignore[arg-type]
    assert [item.team_id for item in ensure_draft(session, player_id, season, now)] == ids  # type: ignore[arg-type]


def test_save_rejects_closed_window() -> None:
    session, season, player_id = database()
    draft = ensure_draft(  # type: ignore[arg-type]
        session, player_id, season, datetime(2026, 7, 31, tzinfo=LONDON)
    )
    with pytest.raises(InvalidPrediction, match="cannot be edited"):
        save_draft(  # type: ignore[arg-type]
            session,
            player_id,
            season,
            [item.team_id for item in draft],
            datetime(2026, 7, 31, tzinfo=LONDON),
        )


def test_database_rejects_duplicate_positions() -> None:
    session, season, player_id = database()
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    draft = ensure_draft(session, player_id, season, now)  # type: ignore[arg-type]
    session.add(
        Prediction(
            player_id=player_id,
            season_id=season.id,  # type: ignore[attr-defined]
            team_id=draft[1].team_id,
            predicted_position=1,
            updated_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_submission_and_resubmission_create_immutable_snapshots() -> None:
    session, season, player_id = database()
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    original = ensure_draft(session, player_id, season, now)  # type: ignore[arg-type]
    status = submit_prediction(session, player_id, season, now)  # type: ignore[arg-type]
    assert status.submitted_order == [item.team_id for item in original]
    moved = move_team(session, player_id, season, original[0].team_id, "down", now)  # type: ignore[arg-type]
    assert has_unsubmitted_changes(moved, status)
    submit_prediction(session, player_id, season, now)  # type: ignore[arg-type]
    snapshots = session.scalars(
        select(PredictionSnapshot).where(PredictionSnapshot.player_id == player_id)
    ).all()
    assert [snapshot.snapshot_type for snapshot in snapshots] == [
        "initial_submission",
        "resubmission",
    ]
    assert snapshots[0].prediction_data != snapshots[1].prediction_data


def test_submission_deadline_is_exclusive() -> None:
    session, season, player_id = database()
    deadline = datetime(2026, 8, 21, tzinfo=LONDON)
    ensure_draft(session, player_id, season, deadline)  # type: ignore[arg-type]
    with pytest.raises(InvalidPrediction, match="cannot be submitted"):
        submit_prediction(session, player_id, season, deadline)  # type: ignore[arg-type]


def test_deadline_locks_last_submission_and_excludes_others_idempotently() -> None:
    session, season, player_id = database()
    now = datetime(2026, 8, 2, tzinfo=LONDON)
    draft = ensure_draft(session, player_id, season, now)  # type: ignore[arg-type]
    submitted_ids = [item.team_id for item in draft]
    submit_prediction(session, player_id, season, now)  # type: ignore[arg-type]
    move_team(session, player_id, season, submitted_ids[0], "down", now)  # type: ignore[arg-type]
    deadline = datetime(2026, 8, 21, tzinfo=LONDON)
    process_deadline(session, season, deadline)  # type: ignore[arg-type]
    status = session.scalar(select(PredictionStatus).where(PredictionStatus.player_id == player_id))
    assert status is not None and status.locked_at is not None
    assert london(status.locked_at) == deadline
    assert [
        item.team_id
        for item in ensure_draft(session, player_id, season, deadline)  # type: ignore[arg-type]
    ] == submitted_ids
    excluded = session.scalars(
        select(PredictionStatus).where(PredictionStatus.excluded_at.is_not(None))
    ).all()
    assert len(excluded) == 4
    process_deadline(session, season, deadline)  # type: ignore[arg-type]
    locks = session.scalars(
        select(PredictionSnapshot).where(PredictionSnapshot.snapshot_type == "deadline_lock")
    ).all()
    assert len(locks) == 1
    with pytest.raises(InvalidPrediction):
        save_draft(session, player_id, season, submitted_ids, deadline)  # type: ignore[arg-type]
