from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.clock import LONDON
from app.db.models import Season, SwapWindow


class GamePhase(StrEnum):
    BEFORE_OPEN = "Not open"
    PREDICTIONS_OPEN = "Predictions open"
    BETWEEN_WINDOWS = "Between swap windows"
    SEASON_CLOSED = "Season closed"


@dataclass(frozen=True)
class Phase:
    label: str
    active_swap: int | None = None


def london(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=LONDON)
    return value.astimezone(LONDON)


def calculate_phase(season: Season, now: datetime) -> Phase:
    now = london(now)
    if now < london(season.game_opens_at):
        return Phase(GamePhase.BEFORE_OPEN)
    if now < london(season.prediction_locks_at):
        return Phase(GamePhase.PREDICTIONS_OPEN)
    for window in season.swap_windows:
        if london(window.opens_at) <= now <= london(window.closes_at):
            return Phase(f"Swap {window.sequence_number} open", window.sequence_number)
    if season.swap_windows and now > london(season.swap_windows[-1].closes_at):
        return Phase(GamePhase.SEASON_CLOSED)
    return Phase(GamePhase.BETWEEN_WINDOWS)


def get_current_season(session: Session) -> Season | None:
    statement = (
        select(Season)
        .options(selectinload(Season.swap_windows))
        .order_by(Season.game_opens_at.desc())
        .limit(1)
    )
    return session.scalar(statement)


def seed_development_season(session: Session) -> Season:
    existing = session.scalar(select(Season).where(Season.name == "2026/27"))
    if existing is not None:
        return existing

    season = Season(
        name="2026/27",
        timezone="Europe/London",
        game_opens_at=datetime(2026, 8, 1, tzinfo=LONDON),
        prediction_locks_at=datetime(2026, 8, 21, tzinfo=LONDON),
        status="scheduled",
    )
    ranges = [
        (datetime(2026, 8, 21, tzinfo=LONDON), datetime(2026, 10, 31, 23, 59, 59, tzinfo=LONDON)),
        (datetime(2026, 11, 1, tzinfo=LONDON), datetime(2026, 12, 31, 23, 59, 59, tzinfo=LONDON)),
        (datetime(2027, 1, 1, tzinfo=LONDON), datetime(2027, 2, 28, 23, 59, 59, tzinfo=LONDON)),
        (datetime(2027, 3, 1, tzinfo=LONDON), datetime(2027, 4, 30, 23, 59, 59, tzinfo=LONDON)),
    ]
    season.swap_windows = [
        SwapWindow(sequence_number=number, opens_at=opens, closes_at=closes)
        for number, (opens, closes) in enumerate(ranges, start=1)
    ]
    session.add(season)
    session.commit()
    return season
