from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Standing, StandingsSnapshot
from app.teams.service import get_season_teams


class InvalidStandings(ValueError):
    pass


@dataclass(frozen=True)
class StandingInput:
    team_id: int
    position: int
    played: int | None = None
    points: int | None = None
    goal_difference: int | None = None


def validate_standings(session: Session, season_id: int, rows: list[StandingInput]) -> None:
    expected_team_ids = {item.team_id for item in get_season_teams(session, season_id)}
    team_ids = [row.team_id for row in rows]
    positions = [row.position for row in rows]
    if len(rows) != 20:
        raise InvalidStandings("Standings must contain exactly 20 teams.")
    if len(set(team_ids)) != 20 or set(team_ids) != expected_team_ids:
        raise InvalidStandings("Standings must contain every season team exactly once.")
    if set(positions) != set(range(1, 21)):
        raise InvalidStandings("Standings must contain every position from 1 to 20 exactly once.")


def create_snapshot(
    session: Session,
    season_id: int,
    rows: list[StandingInput],
    recorded_at: datetime,
    *,
    source: str,
    is_final: bool = False,
) -> StandingsSnapshot:
    validate_standings(session, season_id, rows)
    latest_version = session.scalar(
        select(func.max(StandingsSnapshot.version)).where(
            StandingsSnapshot.season_id == season_id
        )
    )
    snapshot = StandingsSnapshot(
        season_id=season_id,
        version=(latest_version or 0) + 1,
        source=source,
        recorded_at=recorded_at,
        is_final=is_final,
    )
    snapshot.rows = [
        Standing(
            team_id=row.team_id,
            position=row.position,
            played=row.played,
            points=row.points,
            goal_difference=row.goal_difference,
        )
        for row in rows
    ]
    session.add(snapshot)
    session.commit()
    return snapshot


def get_latest_snapshot(session: Session, season_id: int) -> StandingsSnapshot | None:
    statement = (
        select(StandingsSnapshot)
        .options(selectinload(StandingsSnapshot.rows).selectinload(Standing.team))
        .where(StandingsSnapshot.season_id == season_id)
        .order_by(StandingsSnapshot.version.desc())
        .limit(1)
    )
    return session.scalar(statement)


def seed_development_snapshot(
    session: Session, season_id: int, recorded_at: datetime
) -> StandingsSnapshot:
    existing = get_latest_snapshot(session, season_id)
    if existing is not None:
        return existing
    teams = get_season_teams(session, season_id)
    # Stable but deliberately different from design order, so development scores
    # visibly demonstrate penalties without relying on external standings.
    rotated = teams[3:] + teams[:3]
    return create_snapshot(
        session,
        season_id,
        [
            StandingInput(
                team_id=item.team_id,
                position=position,
                played=5,
                points=max(0, 18 - position),
                goal_difference=11 - position,
            )
            for position, item in enumerate(rotated, start=1)
        ],
        recorded_at,
        source="development",
    )
