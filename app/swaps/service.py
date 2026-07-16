from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from app.auth.service import audit
from app.db.models import PredictionSnapshot, Season, Swap, SwapWindow
from app.predictions.service import (
    InvalidPrediction,
    _replace_order,
    _snapshot_data,
    get_draft,
    get_status,
    validate_order,
)
from app.seasons import london


class InvalidSwap(ValueError):
    pass


def active_swap_window(season: Season, now: datetime) -> SwapWindow | None:
    return next(
        (
            window
            for window in season.swap_windows
            if london(window.opens_at) <= now <= london(window.closes_at)
        ),
        None,
    )


def get_swaps(session: Session, player_id: int, season_id: int) -> list[Swap]:
    statement = (
        select(Swap)
        .options(
            selectinload(Swap.swap_window),
            selectinload(Swap.first_team),
            selectinload(Swap.second_team),
        )
        .where(Swap.player_id == player_id, Swap.season_id == season_id)
        .order_by(Swap.created_at, Swap.id)
    )
    return list(session.scalars(statement))


def preview_swap(team_ids: list[int], first_team_id: int, second_team_id: int) -> list[int]:
    if first_team_id == second_team_id:
        raise InvalidSwap("Select two different teams.")
    if first_team_id not in team_ids or second_team_id not in team_ids:
        raise InvalidSwap("Both teams must be part of your prediction.")
    result = list(team_ids)
    first_index = result.index(first_team_id)
    second_index = result.index(second_team_id)
    result[first_index], result[second_index] = result[second_index], result[first_index]
    return result


def validate_swap(
    session: Session,
    player_id: int,
    season: Season,
    first_team_id: int,
    second_team_id: int,
    now: datetime,
) -> tuple[SwapWindow, list[int], list[int]]:
    status = get_status(session, player_id, season.id)
    if status is not None and status.excluded_at is not None:
        raise InvalidSwap("Excluded players cannot make swaps.")
    if status is None or status.locked_at is None:
        raise InvalidSwap("A locked prediction is required before making a swap.")
    window = active_swap_window(season, now)
    if window is None:
        raise InvalidSwap("There is no open swap window.")
    if session.scalar(
        select(Swap.id).where(
            Swap.player_id == player_id,
            Swap.season_id == season.id,
            Swap.swap_window_id == window.id,
        )
    ):
        raise InvalidSwap("Your swap for this window has already been used.")
    before = [item.team_id for item in get_draft(session, player_id, season.id)]
    try:
        validate_order(session, season.id, before)
    except InvalidPrediction as error:
        raise InvalidSwap("Your locked prediction is not valid.") from error
    return window, before, preview_swap(before, first_team_id, second_team_id)


def apply_swap(
    session: Session,
    player_id: int,
    season: Season,
    first_team_id: int,
    second_team_id: int,
    now: datetime,
) -> Swap:
    window, before, after = validate_swap(
        session, player_id, season, first_team_id, second_team_id, now
    )
    first_position = before.index(first_team_id) + 1
    second_position = before.index(second_team_id) + 1
    swap = Swap(
        player_id=player_id,
        season_id=season.id,
        swap_window_id=window.id,
        first_team_id=first_team_id,
        second_team_id=second_team_id,
        first_position=first_position,
        second_position=second_position,
        created_at=now,
    )
    try:
        session.add(swap)
        session.flush()
        session.add_all(
            (
                PredictionSnapshot(
                    player_id=player_id,
                    season_id=season.id,
                    snapshot_type="pre_swap",
                    prediction_data=_snapshot_data(before),
                    created_at=now,
                ),
                PredictionSnapshot(
                    player_id=player_id,
                    season_id=season.id,
                    snapshot_type="post_swap",
                    prediction_data=_snapshot_data(after),
                    created_at=now,
                ),
            )
        )
        _replace_order(session, player_id, season.id, after, now)
        audit(
            session,
            "swap_applied",
            now,
            player_id,
            {"season_id": season.id, "swap_window": window.sequence_number},
        )
        session.commit()
    except (IntegrityError, OperationalError) as error:
        session.rollback()
        raise InvalidSwap("Your swap for this window has already been used.") from error
    return swap
