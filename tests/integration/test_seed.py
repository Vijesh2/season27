from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Base, Season, SwapWindow
from app.seasons import seed_development_season


def test_seed_is_complete_and_idempotent() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first = seed_development_season(session)
        second = seed_development_season(session)
        assert first.id == second.id
        assert session.scalar(select(Season.name)) == "2026/27"
        assert len(session.scalars(select(SwapWindow)).all()) == 4
