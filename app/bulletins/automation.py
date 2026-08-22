import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bulletins.fact_pack import FactPack, build_fact_pack
from app.config import Settings
from app.db.models import Bulletin, Season
from app.results.service import ImportSummary, import_results
from app.results.source import ExternalResult
from app.standings.refresh import RefreshOutcome, refresh_standings
from app.standings.source import StandingsSource


class ResultsSource(Protocol):
    def fetch(
        self, period_start: datetime, period_end: datetime
    ) -> tuple[ExternalResult, ...]: ...


@dataclass(frozen=True)
class PreparedBulletin:
    fact_pack: FactPack
    fact_pack_digest: str
    results_inserted: int
    results_updated: int
    standings_outcome: str
    existing_status: str | None


def fact_pack_digest(pack: FactPack) -> str:
    encoded = json.dumps(pack.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def most_recent_monday_cutoff(now: datetime, timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    monday = local_now.date() - timedelta(days=local_now.weekday())
    cutoff = datetime.combine(monday, time(6), tzinfo=zone)
    if local_now < cutoff:
        cutoff -= timedelta(days=7)
    return cutoff


def bulletin_period(session: Session, season: Season, now: datetime) -> tuple[datetime, datetime]:
    period_end = most_recent_monday_cutoff(now, season.timezone)
    previous = session.scalar(
        select(Bulletin)
        .where(Bulletin.season_id == season.id, Bulletin.status == "published")
        .order_by(Bulletin.period_end.desc())
        .limit(1)
    )
    period_start = previous.period_end if previous is not None else period_end - timedelta(days=7)
    return period_start, period_end


def prepare_bulletin(
    session: Session,
    season: Season,
    results_source: ResultsSource,
    standings_source: StandingsSource,
    settings: Settings,
    now: datetime,
    *,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> PreparedBulletin:
    default_start, default_end = bulletin_period(session, season, now)
    start = period_start or default_start
    end = period_end or default_end
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("A valid timezone-aware bulletin period is required.")

    imported: ImportSummary = import_results(
        session, season.id, results_source.fetch(start, end), now
    )
    refreshed = refresh_standings(
        session, season, standings_source, now, settings, force=True
    )
    if refreshed.outcome == RefreshOutcome.FAILED or refreshed.snapshot is None:
        raise RuntimeError("Current standings could not be refreshed.")

    previous = session.scalar(
        select(Bulletin)
        .where(
            Bulletin.season_id == season.id,
            Bulletin.status == "published",
            Bulletin.period_end == start,
        )
        .limit(1)
    )
    baseline_id: int | None = None
    if previous is not None:
        snapshot = previous.fact_pack.get("current_snapshot", {})
        if isinstance(snapshot, dict) and isinstance(snapshot.get("id"), int):
            baseline_id = int(snapshot["id"])
    pack = build_fact_pack(
        session,
        season.id,
        start,
        end,
        baseline_snapshot_id=baseline_id,
        current_snapshot_id=refreshed.snapshot.id,
    )
    existing = session.scalar(
        select(Bulletin).where(
            Bulletin.season_id == season.id,
            Bulletin.period_end == end,
        )
    )
    return PreparedBulletin(
        fact_pack=pack,
        fact_pack_digest=fact_pack_digest(pack),
        results_inserted=imported.inserted,
        results_updated=imported.updated,
        standings_outcome=refreshed.outcome.value,
        existing_status=existing.status if existing else None,
    )
