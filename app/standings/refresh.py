import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import audit
from app.config import Settings
from app.db.models import (
    Season,
    StandingsRefreshState,
    StandingsRefreshThrottle,
    StandingsSnapshot,
)
from app.seasons import london
from app.standings.service import StandingInput, create_snapshot, get_latest_snapshot
from app.standings.source import (
    ExternalStanding,
    StandingsSource,
    StandingsSourceError,
    normalize_team_name,
)
from app.teams.service import get_season_teams


class RefreshOutcome(StrEnum):
    CACHED = "cached"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    FAILED = "failed"
    THROTTLED = "throttled"


@dataclass(frozen=True)
class RefreshResult:
    outcome: RefreshOutcome
    snapshot: StandingsSnapshot | None


_locks_guard = threading.Lock()
_season_locks: dict[int, threading.Lock] = {}


def _season_lock(season_id: int) -> threading.Lock:
    with _locks_guard:
        return _season_locks.setdefault(season_id, threading.Lock())


def get_refresh_state(session: Session, season_id: int) -> StandingsRefreshState | None:
    return session.scalar(
        select(StandingsRefreshState).where(StandingsRefreshState.season_id == season_id)
    )


def _state(session: Session, season_id: int) -> StandingsRefreshState:
    state = get_refresh_state(session, season_id)
    if state is None:
        state = StandingsRefreshState(season_id=season_id, incident_open=False)
        session.add(state)
        session.flush()
    return state


def _is_fresh(snapshot: StandingsSnapshot | None, now: datetime, settings: Settings) -> bool:
    return bool(
        snapshot
        and now
        < london(snapshot.refreshed_at)
        + timedelta(minutes=settings.standings_cache_minutes)
    )


def _throttled(
    session: Session, keys: tuple[str, ...], now: datetime, settings: Settings
) -> bool:
    threshold = now - timedelta(seconds=settings.standings_refresh_throttle_seconds)
    records = session.scalars(
        select(StandingsRefreshThrottle).where(StandingsRefreshThrottle.key_hash.in_(keys))
    ).all()
    return any(london(record.attempted_at) > threshold for record in records)


def _record_attempt(session: Session, keys: tuple[str, ...], now: datetime) -> None:
    for key in keys:
        record = session.scalar(
            select(StandingsRefreshThrottle).where(StandingsRefreshThrottle.key_hash == key)
        )
        if record is None:
            session.add(StandingsRefreshThrottle(key_hash=key, attempted_at=now))
        else:
            record.attempted_at = now
    session.commit()


def _convert_rows(
    session: Session, season: Season, source_rows: tuple[ExternalStanding, ...]
) -> list[StandingInput]:
    teams = get_season_teams(session, season.id)
    by_identity = {item.team.source_identity: item.team_id for item in teams}
    by_name = {normalize_team_name(item.team.name): item.team_id for item in teams}
    converted: list[StandingInput] = []
    for row in source_rows:
        team_id = by_identity.get(row.identity) or by_name.get(normalize_team_name(row.name))
        if team_id is None:
            raise StandingsSourceError("The source contains an unknown team.")
        converted.append(
            StandingInput(
                team_id=team_id,
                position=row.position,
                played=row.played,
                points=row.points,
                goal_difference=row.goal_difference,
            )
        )
    return converted


def _same_table(
    snapshot: StandingsSnapshot, rows: list[StandingInput], is_final: bool
) -> bool:
    stored = {
        row.team_id: (row.position, row.played, row.points, row.goal_difference)
        for row in snapshot.rows
    }
    imported = {
        row.team_id: (row.position, row.played, row.points, row.goal_difference) for row in rows
    }
    return stored == imported and snapshot.is_final == is_final


def refresh_standings(
    session: Session,
    season: Season,
    source: StandingsSource,
    now: datetime,
    settings: Settings,
    *,
    force: bool = False,
    throttle_keys: tuple[str, ...] = (),
) -> RefreshResult:
    requested_at = now
    latest = get_latest_snapshot(session, season.id)
    if not force and _is_fresh(latest, now, settings):
        return RefreshResult(RefreshOutcome.CACHED, latest)
    state = get_refresh_state(session, season.id)
    if (
        not force
        and state
        and state.last_attempt_at
        and now
        < london(state.last_attempt_at)
        + timedelta(seconds=settings.standings_refresh_throttle_seconds)
    ):
        return RefreshResult(RefreshOutcome.CACHED, latest)
    if force and throttle_keys and _throttled(session, throttle_keys, now, settings):
        return RefreshResult(RefreshOutcome.THROTTLED, latest)
    if force and throttle_keys:
        _record_attempt(session, throttle_keys, now)
    session.commit()
    lock = _season_lock(season.id)
    waited = not lock.acquire(blocking=False)
    if waited:
        lock.acquire()
    try:
        latest = get_latest_snapshot(session, season.id)
        if waited and latest and london(latest.refreshed_at) >= requested_at:
            return RefreshResult(RefreshOutcome.CACHED, latest)
        state = _state(session, season.id)
        state.last_attempt_at = now
        try:
            table = source.fetch()
            rows = _convert_rows(session, season, table.rows)
            if latest and _same_table(latest, rows, table.is_final):
                latest.refreshed_at = now
                snapshot = latest
                outcome = RefreshOutcome.UNCHANGED
            else:
                snapshot = create_snapshot(
                    session,
                    season.id,
                    rows,
                    now,
                    source=source.name,
                    is_final=table.is_final,
                )
                audit(
                    session,
                    "standings_imported",
                    now,
                    metadata={"season_id": season.id, "version": snapshot.version},
                )
                outcome = RefreshOutcome.UPDATED
            state.last_success_at = now
            state.incident_open = False
            session.commit()
            return RefreshResult(outcome, snapshot)
        except Exception:
            session.rollback()
            state = _state(session, season.id)
            state.last_attempt_at = now
            state.last_error_at = now
            if not state.incident_open:
                audit(
                    session,
                    "standings_source_incident",
                    now,
                    metadata={"season_id": season.id},
                )
            state.incident_open = True
            session.commit()
            return RefreshResult(RefreshOutcome.FAILED, get_latest_snapshot(session, season.id))
    finally:
        lock.release()


def snapshot_is_stale(
    snapshot: StandingsSnapshot | None,
    state: StandingsRefreshState | None,
    now: datetime,
    settings: Settings,
) -> bool:
    if snapshot is None:
        return True
    age_stale = now >= london(snapshot.refreshed_at) + timedelta(
        minutes=settings.standings_stale_minutes
    )
    return age_stale or bool(state and state.incident_open)
