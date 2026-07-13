from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.clock import LONDON
from app.db.models import Base, Season
from app.seasons import GamePhase, calculate_phase, seed_development_season


@pytest.fixture
def season() -> Season:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        value = seed_development_season(session)
        windows = list(value.swap_windows)
        session.expunge(value)
        for window in windows:
            session.expunge(window)
        return value


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 7, 31, 23, 59, 59, tzinfo=LONDON), GamePhase.BEFORE_OPEN),
        (datetime(2026, 8, 1, tzinfo=LONDON), GamePhase.PREDICTIONS_OPEN),
        (datetime(2026, 8, 20, 23, 59, 59, tzinfo=LONDON), GamePhase.PREDICTIONS_OPEN),
        (datetime(2026, 8, 21, tzinfo=LONDON), "Swap 1 open"),
        (datetime(2026, 10, 31, 23, 59, 59, tzinfo=LONDON), "Swap 1 open"),
        (datetime(2026, 11, 1, tzinfo=LONDON), "Swap 2 open"),
        (datetime(2027, 1, 1, tzinfo=LONDON), "Swap 3 open"),
        (datetime(2027, 3, 1, tzinfo=LONDON), "Swap 4 open"),
        (datetime(2027, 5, 1, tzinfo=LONDON), GamePhase.SEASON_CLOSED),
    ],
)
def test_phase_boundaries(season: Season, now: datetime, expected: str) -> None:
    assert calculate_phase(season, now).label == expected


def test_between_windows_is_supported(season: Season) -> None:
    season.swap_windows[1].opens_at += timedelta(hours=1)
    phase = calculate_phase(season, datetime(2026, 11, 1, tzinfo=LONDON))
    assert phase.label == GamePhase.BETWEEN_WINDOWS
