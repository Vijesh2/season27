from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.bulletins import BULLETIN_TITLE
from app.bulletins.impact import (
    MatchImpact,
    PlayerImpact,
    calculate_player_impacts,
    result_matches_transition,
)
from app.db.models import (
    FootballResult,
    Player,
    PredictionSnapshot,
    Season,
    Standing,
    StandingsSnapshot,
)


class FactPackError(ValueError):
    pass


@dataclass(frozen=True)
class MatchFact:
    result_id: int
    event_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    played_at: str
    source_url: str
    evidence: str


@dataclass(frozen=True)
class SnapshotFact:
    id: int
    version: int
    recorded_at: str


@dataclass(frozen=True)
class PredictionChangeFact:
    player_id: int
    display_name: str
    change_type: str
    changed_at: str


@dataclass(frozen=True)
class FactPack:
    bulletin_title: str
    season_id: int
    season_name: str
    period_start: str
    period_end: str
    baseline_snapshot: SnapshotFact
    current_snapshot: SnapshotFact
    matches: tuple[MatchFact, ...]
    prediction_changes: tuple[PredictionChangeFact, ...]
    period_player_impacts: tuple[PlayerImpact, ...]
    verified_match_impacts: tuple[MatchImpact, ...]
    claim_rules: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc(value: datetime, naive_timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=naive_timezone)
    return value.astimezone(UTC)


def _iso(value: datetime, naive_timezone: ZoneInfo) -> str:
    return _utc(value, naive_timezone).isoformat().replace("+00:00", "Z")


def _snapshots(session: Session, season_id: int) -> list[StandingsSnapshot]:
    return list(
        session.scalars(
            select(StandingsSnapshot)
            .options(selectinload(StandingsSnapshot.rows).selectinload(Standing.team))
            .where(StandingsSnapshot.season_id == season_id)
            .order_by(StandingsSnapshot.version)
        )
    )


def _choose_snapshots(
    session: Session,
    season: Season,
    period_start: datetime,
    period_end: datetime,
    baseline_snapshot_id: int | None,
    current_snapshot_id: int | None,
) -> tuple[StandingsSnapshot, StandingsSnapshot, list[StandingsSnapshot]]:
    timezone = ZoneInfo(season.timezone)
    snapshots = _snapshots(session, season.id)
    by_id = {item.id: item for item in snapshots}
    baseline = by_id.get(baseline_snapshot_id) if baseline_snapshot_id is not None else next(
        (
            item
            for item in reversed(snapshots)
            if _utc(item.recorded_at, timezone) <= period_start.astimezone(UTC)
        ),
        snapshots[0] if snapshots else None,
    )
    current = by_id.get(current_snapshot_id) if current_snapshot_id is not None else next(
        (
            item
            for item in reversed(snapshots)
            if _utc(item.recorded_at, timezone) <= period_end.astimezone(UTC)
        ),
        None,
    )
    if baseline is None or current is None:
        raise FactPackError(
            "The bulletin period requires baseline and current standings snapshots."
        )
    if baseline.season_id != season.id or current.season_id != season.id:
        raise FactPackError("A selected standings snapshot belongs to another season.")
    if baseline.version > current.version:
        raise FactPackError("The current standings snapshot cannot precede the baseline snapshot.")
    transitions = [
        item for item in snapshots if baseline.version <= item.version <= current.version
    ]
    return baseline, current, transitions


def _results(
    session: Session,
    period_start: datetime,
    period_end: datetime,
) -> list[FootballResult]:
    stored = list(
        session.scalars(
            select(FootballResult)
            .options(
                selectinload(FootballResult.home_team),
                selectinload(FootballResult.away_team),
            )
            .order_by(FootballResult.scheduled_at, FootballResult.source_event_id)
        )
    )
    return [
        item
        for item in stored
        if period_start.astimezone(UTC)
        <= _utc(item.completed_at or item.scheduled_at, ZoneInfo("UTC"))
        < period_end.astimezone(UTC)
    ]


def _verified_impacts(
    session: Session,
    season_id: int,
    snapshots: list[StandingsSnapshot],
    results: list[FootballResult],
    prediction_changes: list[PredictionSnapshot],
    snapshot_timezone: ZoneInfo,
) -> tuple[MatchImpact, ...]:
    verified: list[MatchImpact] = []
    for before, after in zip(snapshots, snapshots[1:], strict=False):
        start = _utc(before.recorded_at, snapshot_timezone)
        end = _utc(after.recorded_at, snapshot_timezone)
        candidates = [
            item
            for item in results
            if start
            < _utc(item.completed_at or item.scheduled_at, ZoneInfo("UTC"))
            <= end
        ]
        intervening_predictions = [
            item
            for item in prediction_changes
            if start < _utc(item.created_at, snapshot_timezone) <= end
        ]
        if (
            len(candidates) != 1
            or intervening_predictions
            or not result_matches_transition(candidates[0], before, after)
        ):
            continue
        result = candidates[0]
        verified.append(
            MatchImpact(
                result_id=result.id,
                event_id=result.source_event_id,
                home_team=result.home_team.name,
                away_team=result.away_team.name,
                home_score=result.home_score,
                away_score=result.away_score,
                before_snapshot_id=before.id,
                after_snapshot_id=after.id,
                player_impacts=calculate_player_impacts(session, season_id, before, after),
            )
        )
    return tuple(verified)


def _prediction_changes(
    session: Session,
    season_id: int,
    period_start: datetime,
    period_end: datetime,
    timezone: ZoneInfo,
) -> list[PredictionSnapshot]:
    changes = list(
        session.scalars(
            select(PredictionSnapshot)
            .where(PredictionSnapshot.season_id == season_id)
            .order_by(PredictionSnapshot.created_at, PredictionSnapshot.id)
        )
    )
    return [
        item
        for item in changes
        if period_start.astimezone(UTC)
        <= _utc(item.created_at, timezone)
        < period_end.astimezone(UTC)
        and item.snapshot_type
        in {"post_swap", "admin_correction", "admin_reinstatement"}
    ]


def build_fact_pack(
    session: Session,
    season_id: int,
    period_start: datetime,
    period_end: datetime,
    *,
    baseline_snapshot_id: int | None = None,
    current_snapshot_id: int | None = None,
) -> FactPack:
    if period_start.tzinfo is None or period_end.tzinfo is None or period_end <= period_start:
        raise FactPackError("A valid timezone-aware bulletin period is required.")
    season = session.get(Season, season_id)
    if season is None:
        raise FactPackError("The bulletin season was not found.")
    timezone = ZoneInfo(season.timezone)
    baseline, current, transitions = _choose_snapshots(
        session,
        season,
        period_start,
        period_end,
        baseline_snapshot_id,
        current_snapshot_id,
    )
    results = _results(session, period_start, period_end)
    prediction_changes = _prediction_changes(
        session, season_id, period_start, period_end, timezone
    )
    verified = _verified_impacts(
        session, season_id, transitions, results, prediction_changes, timezone
    )
    verified_ids = {item.result_id for item in verified}
    players = {
        item.id: item
        for item in session.scalars(
            select(Player).where(
                Player.id.in_({item.player_id for item in prediction_changes})
            )
        )
    }
    return FactPack(
        bulletin_title=BULLETIN_TITLE,
        season_id=season.id,
        season_name=season.name,
        period_start=period_start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        period_end=period_end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        baseline_snapshot=SnapshotFact(
            baseline.id, baseline.version, _iso(baseline.recorded_at, timezone)
        ),
        current_snapshot=SnapshotFact(
            current.id, current.version, _iso(current.recorded_at, timezone)
        ),
        matches=tuple(
            MatchFact(
                result_id=item.id,
                event_id=item.source_event_id,
                home_team=item.home_team.name,
                away_team=item.away_team.name,
                home_score=item.home_score,
                away_score=item.away_score,
                played_at=_iso(item.completed_at or item.scheduled_at, ZoneInfo("UTC")),
                source_url=item.source_url,
                evidence="verified_single_match_impact"
                if item.id in verified_ids
                else "period_context_only",
            )
            for item in results
        ),
        prediction_changes=tuple(
            PredictionChangeFact(
                player_id=item.player_id,
                display_name=players[item.player_id].display_name,
                change_type=item.snapshot_type,
                changed_at=_iso(item.created_at, timezone),
            )
            for item in prediction_changes
        ),
        period_player_impacts=calculate_player_impacts(
            session, season_id, baseline, current, include_unchanged=True
        ),
        verified_match_impacts=verified,
        claim_rules=(
            "Exact scorelines may be quoted from matches.",
            "Only verified_match_impacts may be described as causing a leaderboard change.",
            "Other matches are period context and must not be assigned individual causation.",
            "Period changes may include listed prediction swaps or corrections.",
            "Player ranks and scores must be quoted exactly as supplied.",
        )
        + (
            (
                "Leaderboard movement before the baseline snapshot was recorded was not measured "
                "and must not be claimed."
            ),
        )
        if _utc(baseline.recorded_at, timezone) > period_start.astimezone(UTC)
        else (
            "Exact scorelines may be quoted from matches.",
            "Only verified_match_impacts may be described as causing a leaderboard change.",
            "Other matches are period context and must not be assigned individual causation.",
            "Period changes may include listed prediction swaps or corrections.",
            "Player ranks and scores must be quoted exactly as supplied.",
        ),
    )
