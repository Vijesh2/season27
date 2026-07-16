from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock, Thread
from time import sleep

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.clock import LONDON
from app.config import Settings
from app.db.models import AuditEvent, Season, StandingsSnapshot
from app.db.session import create_database_engine, create_schema
from app.seasons import seed_development_season
from app.standings.refresh import (
    RefreshOutcome,
    get_refresh_state,
    refresh_standings,
    snapshot_is_stale,
)
from app.standings.source import ExternalStanding, SourceTable, StandingsSourceError
from app.teams.service import get_season_teams, seed_fixed_teams


class ControlledSource:
    name = "test"

    def __init__(self, table: SourceTable, *, delay: float = 0) -> None:
        self.table = table
        self.delay = delay
        self.error = False
        self.calls = 0
        self.lock = Lock()

    def fetch(self) -> SourceTable:
        with self.lock:
            self.calls += 1
        if self.delay:
            sleep(self.delay)
        if self.error:
            raise StandingsSourceError("controlled private failure")
        return self.table


def database(path: Path) -> tuple[Engine, Season, SourceTable]:
    engine = create_database_engine(f"sqlite:///{path}")
    create_schema(engine)
    with Session(engine, expire_on_commit=False) as session:
        season = seed_development_season(session)
        seed_fixed_teams(session, season)
        teams = get_season_teams(session, season.id)
        table = SourceTable(
            rows=tuple(
                ExternalStanding(
                    identity=item.team.source_identity,
                    name=item.team.name,
                    position=position,
                    played=10,
                    points=30 - position,
                    goal_difference=10 - position,
                )
                for position, item in enumerate(teams, start=1)
            ),
            is_final=False,
        )
    return engine, season, table


def test_cache_boundary_and_unchanged_refresh_update_freshness(tmp_path: Path) -> None:
    engine, season, table = database(tmp_path / "cache.db")
    source = ControlledSource(table)
    settings = Settings(database_url="sqlite://")
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    with Session(engine, expire_on_commit=False) as session:
        first = refresh_standings(session, season, source, now, settings)
        assert first.outcome == RefreshOutcome.UPDATED
        assert first.snapshot is not None and first.snapshot.version == 1
        cached = refresh_standings(
            session, season, source, now + timedelta(minutes=14, seconds=59), settings
        )
        assert cached.outcome == RefreshOutcome.CACHED
        exact_boundary = refresh_standings(
            session, season, source, now + timedelta(minutes=15), settings
        )
        assert exact_boundary.outcome == RefreshOutcome.UNCHANGED
        assert exact_boundary.snapshot is not None
        assert exact_boundary.snapshot.version == 1
        assert exact_boundary.snapshot.refreshed_at.replace(tzinfo=LONDON) == now + timedelta(
            minutes=15
        )
    assert source.calls == 2


def test_changed_and_final_table_create_new_version(tmp_path: Path) -> None:
    engine, season, table = database(tmp_path / "changed.db")
    source = ControlledSource(table)
    settings = Settings(database_url="sqlite://")
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    with Session(engine, expire_on_commit=False) as session:
        refresh_standings(session, season, source, now, settings)
        source.table = SourceTable(
            rows=tuple(
                replace(row, position=21 - row.position, played=38) for row in table.rows
            ),
            is_final=True,
        )
        result = refresh_standings(
            session, season, source, now + timedelta(minutes=15), settings
        )
        assert result.outcome == RefreshOutcome.UPDATED
        assert result.snapshot is not None
        assert result.snapshot.version == 2 and result.snapshot.is_final
        assert session.scalar(select(func.count(StandingsSnapshot.id))) == 2


def test_invalid_source_data_never_replaces_valid_snapshot(tmp_path: Path) -> None:
    engine, season, table = database(tmp_path / "invalid.db")
    source = ControlledSource(table)
    settings = Settings(database_url="sqlite://")
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    with Session(engine, expire_on_commit=False) as session:
        valid = refresh_standings(session, season, source, now, settings).snapshot
        source.table = SourceTable(
            rows=(replace(table.rows[0], identity="unknown", name="Unknown FC"), *table.rows[1:]),
            is_final=False,
        )
        failed = refresh_standings(
            session, season, source, now + timedelta(minutes=15), settings
        )
        assert failed.outcome == RefreshOutcome.FAILED
        assert failed.snapshot is not None and valid is not None
        assert failed.snapshot.id == valid.id
        assert session.scalar(select(func.count(StandingsSnapshot.id))) == 1


def test_failures_preserve_snapshot_open_one_incident_and_mark_stale(tmp_path: Path) -> None:
    engine, season, table = database(tmp_path / "failure.db")
    source = ControlledSource(table)
    settings = Settings(database_url="sqlite://", standings_stale_minutes=30)
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    with Session(engine, expire_on_commit=False) as session:
        snapshot = refresh_standings(session, season, source, now, settings).snapshot
        source.error = True
        for minutes in (15, 30):
            result = refresh_standings(
                session, season, source, now + timedelta(minutes=minutes), settings
            )
            assert result.outcome == RefreshOutcome.FAILED
            assert result.snapshot is not None and snapshot is not None
            assert result.snapshot.id == snapshot.id
            immediate = refresh_standings(
                session,
                season,
                source,
                now + timedelta(minutes=minutes, seconds=1),
                settings,
            )
            assert immediate.outcome == RefreshOutcome.CACHED
        state = get_refresh_state(session, season.id)
        assert state is not None and state.incident_open
        assert snapshot_is_stale(snapshot, state, now + timedelta(minutes=30), settings)
        incidents = session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "standings_source_incident")
        ).all()
        assert len(incidents) == 1
        assert "failure" not in str(incidents[0].event_metadata)
        assert source.calls == 3


def test_explicit_refresh_is_throttled_by_any_key(tmp_path: Path) -> None:
    engine, season, table = database(tmp_path / "throttle.db")
    source = ControlledSource(table)
    settings = Settings(database_url="sqlite://", standings_refresh_throttle_seconds=60)
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    with Session(engine, expire_on_commit=False) as session:
        first = refresh_standings(
            session,
            season,
            source,
            now,
            settings,
            force=True,
            throttle_keys=("session-a", "ip-a"),
        )
        second = refresh_standings(
            session,
            season,
            source,
            now + timedelta(seconds=59),
            settings,
            force=True,
            throttle_keys=("session-b", "ip-a"),
        )
        assert first.outcome == RefreshOutcome.UPDATED
        assert second.outcome == RefreshOutcome.THROTTLED
    assert source.calls == 1


def test_simultaneous_stale_visits_use_one_source_request(tmp_path: Path) -> None:
    engine, detached_season, table = database(tmp_path / "single-flight.db")
    source = ControlledSource(table, delay=0.2)
    settings = Settings(database_url="sqlite://")
    now = datetime(2026, 9, 1, 12, tzinfo=LONDON)
    outcomes: list[RefreshOutcome] = []

    def visit() -> None:
        with Session(engine) as session:
            season = session.get(Season, detached_season.id)
            assert season is not None
            result = refresh_standings(session, season, source, now, settings)
            outcomes.append(result.outcome)

    threads = (Thread(target=visit), Thread(target=visit))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert source.calls == 1
    assert sorted(outcomes) == [RefreshOutcome.CACHED, RefreshOutcome.UPDATED]


def test_name_normalization_is_used_when_source_identity_changes(tmp_path: Path) -> None:
    engine, season, table = database(tmp_path / "names.db")
    city_index = next(i for i, row in enumerate(table.rows) if row.name == "Manchester City")
    rows = list(table.rows)
    rows[city_index] = replace(rows[city_index], identity="changed-id", name="Man City")
    source = ControlledSource(SourceTable(tuple(rows), is_final=False))
    with Session(engine) as session:
        result = refresh_standings(
            session,
            season,
            source,
            datetime(2026, 9, 1, 12, tzinfo=LONDON),
            Settings(database_url="sqlite://"),
        )
        assert result.outcome == RefreshOutcome.UPDATED
