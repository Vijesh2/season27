from datetime import datetime
from pathlib import Path
from threading import Barrier, Thread

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.auth.service import seed_development_players
from app.clock import LONDON
from app.db.models import AuditEvent, Base, PredictionSnapshot, Season, Swap
from app.predictions.service import ensure_draft, get_draft, process_deadline, submit_prediction
from app.seasons import seed_development_season
from app.swaps.service import InvalidSwap, active_swap_window, apply_swap, preview_swap
from app.teams.service import seed_fixed_teams


def locked_database() -> tuple[Session, Season, int, list[int]]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    before_deadline = datetime(2026, 8, 20, 12, tzinfo=LONDON)
    season = seed_development_season(session)
    seed_fixed_teams(session, season)
    player = seed_development_players(session, before_deadline)[1]
    draft = ensure_draft(session, player.id, season, before_deadline)
    original = [item.team_id for item in draft]
    submit_prediction(session, player.id, season, before_deadline)
    process_deadline(session, season, datetime(2026, 8, 21, tzinfo=LONDON))
    return session, season, player.id, original


def test_preview_exchanges_exactly_two_positions_without_persisting() -> None:
    session, season, player_id, original = locked_database()
    preview = preview_swap(original, original[0], original[19])
    assert preview[0] == original[19]
    assert preview[19] == original[0]
    assert preview[1:19] == original[1:19]
    assert [item.team_id for item in get_draft(session, player_id, season.id)] == original


@pytest.mark.parametrize("selection", [(1, 1), (1, 99999), (99998, 99999)])
def test_preview_rejects_invalid_team_selections(selection: tuple[int, int]) -> None:
    with pytest.raises(InvalidSwap):
        preview_swap([1, 2, 3], *selection)


def test_window_boundaries_are_inclusive_and_do_not_carry_forward() -> None:
    session, season, _, _ = locked_database()
    assert active_swap_window(season, datetime(2026, 8, 20, 23, 59, 59, tzinfo=LONDON)) is None  # type: ignore[arg-type]
    assert active_swap_window(season, datetime(2026, 8, 21, tzinfo=LONDON)).sequence_number == 1  # type: ignore[arg-type,union-attr]
    assert active_swap_window(  # type: ignore[arg-type]
        season, datetime(2026, 10, 31, 23, 59, 59, tzinfo=LONDON)
    ).sequence_number == 1  # type: ignore[union-attr]
    assert active_swap_window(season, datetime(2026, 11, 1, tzinfo=LONDON)).sequence_number == 2  # type: ignore[arg-type,union-attr]
    session.close()


def test_apply_swap_is_atomic_audited_snapshotted_and_once_per_window() -> None:
    session, season, player_id, original = locked_database()
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    swap = apply_swap(session, player_id, season, original[0], original[19], now)  # type: ignore[arg-type]
    assert (swap.first_position, swap.second_position) == (1, 20)
    updated = [item.team_id for item in get_draft(session, player_id, season.id)]  # type: ignore[attr-defined]
    assert updated[0] == original[19] and updated[19] == original[0]
    snapshots = session.scalars(
        select(PredictionSnapshot).where(
            PredictionSnapshot.player_id == player_id,
            PredictionSnapshot.snapshot_type.in_(("pre_swap", "post_swap")),
        )
    ).all()
    assert [item.snapshot_type for item in snapshots] == ["pre_swap", "post_swap"]
    assert snapshots[0].prediction_data != snapshots[1].prediction_data
    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "swap_applied"))
    assert event is not None
    assert event.event_metadata == {"season_id": season.id, "swap_window": 1}
    assert "prediction" not in event.event_metadata
    with pytest.raises(InvalidSwap, match="already been used"):
        apply_swap(session, player_id, season, original[1], original[2], now)  # type: ignore[arg-type]
    assert session.scalar(select(Swap).where(Swap.player_id == player_id)) is swap
    next_swap = apply_swap(  # type: ignore[arg-type]
        session,
        player_id,
        season,
        updated[1],
        updated[2],
        datetime(2026, 11, 1, tzinfo=LONDON),
    )
    assert next_swap.swap_window.sequence_number == 2
    assert len(session.scalars(select(Swap).where(Swap.player_id == player_id)).all()) == 2


def test_swap_rejects_closed_window_and_excluded_player() -> None:
    session, season, player_id, original = locked_database()
    with pytest.raises(InvalidSwap, match="no open"):
        apply_swap(  # type: ignore[arg-type]
            session,
            player_id,
            season,
            original[0],
            original[1],
            datetime(2026, 8, 20, 23, 59, 59, tzinfo=LONDON),
        )
    excluded_id = seed_development_players(
        session, datetime(2026, 8, 20, tzinfo=LONDON)
    )[2].id
    with pytest.raises(InvalidSwap, match="Excluded"):
        apply_swap(  # type: ignore[arg-type]
            session,
            excluded_id,
            season,
            original[0],
            original[1],
            datetime(2026, 9, 1, tzinfo=LONDON),
        )


def test_database_constraint_prevents_two_swaps_consuming_one_window(tmp_path: Path) -> None:
    database_path = tmp_path / "concurrent.db"
    engine = create_engine(f"sqlite:///{database_path}", connect_args={"timeout": 10})
    Base.metadata.create_all(engine)
    before_deadline = datetime(2026, 8, 20, 12, tzinfo=LONDON)
    with Session(engine, expire_on_commit=False) as setup:
        season = seed_development_season(setup)
        seed_fixed_teams(setup, season)
        player = seed_development_players(setup, before_deadline)[1]
        original = [
            item.team_id for item in ensure_draft(setup, player.id, season, before_deadline)
        ]
        submit_prediction(setup, player.id, season, before_deadline)
        process_deadline(setup, season, datetime(2026, 8, 21, tzinfo=LONDON))
        player_id = player.id
        season_id = season.id

    barrier = Barrier(2)
    outcomes: list[str] = []

    def attempt(first_id: int, second_id: int) -> None:
        with Session(engine) as worker:
            season = worker.get(Season, season_id)
            assert season is not None
            _ = season.swap_windows
            barrier.wait()
            try:
                apply_swap(
                    worker,
                    player_id,
                    season,
                    first_id,
                    second_id,
                    datetime(2026, 9, 1, tzinfo=LONDON),
                )
                outcomes.append("applied")
            except InvalidSwap:
                outcomes.append("rejected")

    threads = (
        Thread(target=attempt, args=(original[0], original[-1])),
        Thread(target=attempt, args=(original[1], original[-2])),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["applied", "rejected"]
    with Session(engine) as verification:
        assert len(verification.scalars(select(Swap)).all()) == 1
        assert len(
            verification.scalars(
                select(PredictionSnapshot).where(
                    PredictionSnapshot.snapshot_type.in_(("pre_swap", "post_swap"))
                )
            ).all()
        ) == 2
