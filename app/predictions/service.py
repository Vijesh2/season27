from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.auth.service import audit
from app.db.models import (
    Player,
    Prediction,
    PredictionSnapshot,
    PredictionStatus,
    Season,
)
from app.seasons import london
from app.teams.service import get_season_teams


class InvalidPrediction(ValueError):
    pass


def editing_is_open(season: Season, now: datetime) -> bool:
    return london(season.game_opens_at) <= now < london(season.prediction_locks_at)


def get_draft(session: Session, player_id: int, season_id: int) -> list[Prediction]:
    statement = (
        select(Prediction)
        .options(selectinload(Prediction.team))
        .where(Prediction.player_id == player_id, Prediction.season_id == season_id)
        .order_by(Prediction.predicted_position)
    )
    return list(session.scalars(statement))


def get_status(session: Session, player_id: int, season_id: int) -> PredictionStatus | None:
    return session.scalar(
        select(PredictionStatus).where(
            PredictionStatus.player_id == player_id,
            PredictionStatus.season_id == season_id,
        )
    )


def ensure_draft(
    session: Session, player_id: int, season: Season, now: datetime
) -> list[Prediction]:
    draft = get_draft(session, player_id, season.id)
    if draft:
        return draft
    teams = get_season_teams(session, season.id)
    session.add_all(
        Prediction(
            player_id=player_id,
            season_id=season.id,
            team_id=item.team_id,
            predicted_position=position,
            updated_at=now,
        )
        for position, item in enumerate(teams, start=1)
    )
    session.commit()
    return get_draft(session, player_id, season.id)


def validate_order(session: Session, season_id: int, team_ids: list[int]) -> None:
    expected = {item.team_id for item in get_season_teams(session, season_id)}
    if len(team_ids) != 20:
        raise InvalidPrediction("A prediction must contain exactly 20 teams.")
    if len(set(team_ids)) != len(team_ids):
        raise InvalidPrediction("A prediction cannot contain duplicate teams.")
    if set(team_ids) != expected:
        raise InvalidPrediction("A prediction must contain every season team exactly once.")


def save_draft(
    session: Session,
    player_id: int,
    season: Season,
    team_ids: list[int],
    now: datetime,
) -> list[Prediction]:
    if not editing_is_open(season, now):
        raise InvalidPrediction("Predictions cannot be edited at this time.")
    status = get_status(session, player_id, season.id)
    if status and (status.locked_at or status.excluded_at):
        raise InvalidPrediction("This prediction is permanently locked.")
    validate_order(session, season.id, team_ids)
    try:
        session.execute(
            delete(Prediction).where(
                Prediction.player_id == player_id,
                Prediction.season_id == season.id,
            )
        )
        session.flush()
        session.add_all(
            Prediction(
                player_id=player_id,
                season_id=season.id,
                team_id=team_id,
                predicted_position=position,
                updated_at=now,
            )
            for position, team_id in enumerate(team_ids, start=1)
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return get_draft(session, player_id, season.id)


def _snapshot_data(team_ids: list[int]) -> list[dict[str, int]]:
    return [
        {"team_id": team_id, "predicted_position": position}
        for position, team_id in enumerate(team_ids, start=1)
    ]


def submit_prediction(
    session: Session, player_id: int, season: Season, now: datetime
) -> PredictionStatus:
    if not editing_is_open(season, now):
        raise InvalidPrediction("Predictions cannot be submitted at this time.")
    draft = get_draft(session, player_id, season.id)
    team_ids = [item.team_id for item in draft]
    validate_order(session, season.id, team_ids)
    status = get_status(session, player_id, season.id)
    if status and (status.locked_at or status.excluded_at):
        raise InvalidPrediction("This prediction is permanently locked.")
    snapshot_type = "resubmission" if status and status.submitted_at else "initial_submission"
    if status is None:
        status = PredictionStatus(player_id=player_id, season_id=season.id)
        session.add(status)
    status.submitted_at = now
    status.submitted_order = team_ids
    session.add(
        PredictionSnapshot(
            player_id=player_id,
            season_id=season.id,
            snapshot_type=snapshot_type,
            prediction_data=_snapshot_data(team_ids),
            created_at=now,
        )
    )
    audit(session, "prediction_submitted", now, player_id, {"season_id": season.id})
    session.commit()
    return status


def has_unsubmitted_changes(draft: list[Prediction], status: PredictionStatus | None) -> bool:
    if status is None or status.submitted_order is None:
        return False
    return [item.team_id for item in draft] != status.submitted_order


def _replace_order(
    session: Session,
    player_id: int,
    season_id: int,
    team_ids: list[int],
    now: datetime,
) -> None:
    session.execute(
        delete(Prediction).where(
            Prediction.player_id == player_id,
            Prediction.season_id == season_id,
        )
    )
    session.flush()
    session.add_all(
        Prediction(
            player_id=player_id,
            season_id=season_id,
            team_id=team_id,
            predicted_position=position,
            updated_at=now,
        )
        for position, team_id in enumerate(team_ids, start=1)
    )


def process_deadline(session: Session, season: Season, now: datetime) -> None:
    if now < london(season.prediction_locks_at):
        return
    for player in session.scalars(select(Player).where(Player.is_active.is_(True))):
        status = get_status(session, player.id, season.id)
        if status and (status.locked_at or status.excluded_at):
            continue
        if status is None:
            status = PredictionStatus(player_id=player.id, season_id=season.id)
            session.add(status)
        if status.submitted_order:
            validate_order(session, season.id, status.submitted_order)
            _replace_order(session, player.id, season.id, status.submitted_order, now)
            status.locked_at = now
            session.add(
                PredictionSnapshot(
                    player_id=player.id,
                    season_id=season.id,
                    snapshot_type="deadline_lock",
                    prediction_data=_snapshot_data(status.submitted_order),
                    created_at=now,
                )
            )
            audit(session, "prediction_locked", now, player.id, {"season_id": season.id})
        else:
            status.excluded_at = now
            audit(session, "player_excluded", now, player.id, {"season_id": season.id})
    session.commit()


def move_team(
    session: Session,
    player_id: int,
    season: Season,
    team_id: int,
    direction: str,
    now: datetime,
) -> list[Prediction]:
    draft = ensure_draft(session, player_id, season, now)
    ids = [item.team_id for item in draft]
    if team_id not in ids or direction not in {"up", "down"}:
        raise InvalidPrediction("Invalid move.")
    index = ids.index(team_id)
    target = index - 1 if direction == "up" else index + 1
    if target < 0 or target >= len(ids):
        return draft
    ids[index], ids[target] = ids[target], ids[index]
    return save_draft(session, player_id, season, ids, now)
