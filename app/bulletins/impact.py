from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    FootballResult,
    Player,
    Prediction,
    PredictionSnapshot,
    PredictionStatus,
    Season,
    StandingsSnapshot,
)
from app.leaderboard.service import LeaderboardEntry, _positions_for_scoring
from app.scoring import rank_scores, score_prediction


@dataclass(frozen=True)
class TeamPenaltyChange:
    team_id: int
    team_name: str
    previous_position: int
    current_position: int
    previous_penalty: int
    current_penalty: int
    penalty_change: int


@dataclass(frozen=True)
class PlayerImpact:
    player_id: int
    display_name: str
    previous_rank: int
    current_rank: int
    rank_change: int
    previous_score: int
    current_score: int
    score_change: int
    team_changes: tuple[TeamPenaltyChange, ...]


@dataclass(frozen=True)
class MatchImpact:
    result_id: int
    event_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    before_snapshot_id: int
    after_snapshot_id: int
    player_impacts: tuple[PlayerImpact, ...]


def calculate_player_impacts(
    session: Session,
    season_id: int,
    before: StandingsSnapshot,
    after: StandingsSnapshot,
    *,
    include_unchanged: bool = False,
) -> tuple[PlayerImpact, ...]:
    previous = {
        item.player.id: item for item in _historical_leaderboard(session, season_id, before)
    }
    current = {
        item.player.id: item for item in _historical_leaderboard(session, season_id, after)
    }
    if previous.keys() != current.keys():
        raise ValueError("Leaderboard eligibility changed between standings snapshots.")
    team_names = {row.team_id: row.team.name for row in after.rows}
    impacts: list[PlayerImpact] = []
    for player_id, old_entry in previous.items():
        new_entry = current[player_id]
        impact = _player_impact(old_entry, new_entry, team_names)
        if include_unchanged or impact.rank_change or impact.score_change:
            impacts.append(impact)
    return tuple(
        sorted(
            impacts,
            key=lambda item: (
                -abs(item.rank_change),
                -abs(item.score_change),
                item.current_rank,
                item.display_name.casefold(),
            ),
        )
    )


def _utc(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.astimezone(UTC)


def _prediction_as_of(
    session: Session,
    player_id: int,
    season_id: int,
    when: datetime,
    timezone: ZoneInfo,
) -> dict[int, int]:
    history = list(
        session.scalars(
            select(PredictionSnapshot)
            .where(
                PredictionSnapshot.player_id == player_id,
                PredictionSnapshot.season_id == season_id,
            )
            .order_by(PredictionSnapshot.created_at, PredictionSnapshot.id)
        )
    )
    available = [item for item in history if _utc(item.created_at, timezone) <= when]
    if available:
        return {
            int(item["team_id"]): int(item["predicted_position"])
            for item in available[-1].prediction_data
        }
    if history:
        raise ValueError("A player has no prediction snapshot at the standings time.")
    predictions = session.scalars(
        select(Prediction).where(
            Prediction.player_id == player_id,
            Prediction.season_id == season_id,
        )
    ).all()
    return {item.team_id: item.predicted_position for item in predictions}


def _historical_leaderboard(
    session: Session,
    season_id: int,
    snapshot: StandingsSnapshot,
) -> list[LeaderboardEntry]:
    season = session.get(Season, season_id)
    if season is None:
        raise ValueError("The leaderboard season was not found.")
    timezone = ZoneInfo(season.timezone)
    snapshot_time = _utc(snapshot.recorded_at, timezone)
    candidates = session.execute(
        select(Player, PredictionStatus)
        .join(PredictionStatus, PredictionStatus.player_id == Player.id)
        .where(
            PredictionStatus.season_id == season_id,
            PredictionStatus.locked_at.is_not(None),
            Player.is_active.is_(True),
        )
    ).all()
    players = {
        player.id: player
        for player, status in candidates
        if status.locked_at is not None
        and _utc(status.locked_at, timezone) <= snapshot_time
        and (
            status.excluded_at is None
            or _utc(status.excluded_at, timezone) > snapshot_time
        )
    }
    scores = [
        score_prediction(
            player_id,
            _prediction_as_of(session, player_id, season_id, snapshot_time, timezone),
            _positions_for_scoring(snapshot),
        )
        for player_id in players
    ]
    return [
        LeaderboardEntry(player=players[item.player_id], score=item)
        for item in rank_scores(scores)
    ]


def _player_impact(
    previous: LeaderboardEntry,
    current: LeaderboardEntry,
    team_names: dict[int, str],
) -> PlayerImpact:
    previous_teams = {item.team_id: item for item in previous.score.breakdown}
    current_teams = {item.team_id: item for item in current.score.breakdown}
    team_changes = tuple(
        sorted(
            (
                TeamPenaltyChange(
                    team_id=team_id,
                    team_name=team_names[team_id],
                    previous_position=old.actual_position,
                    current_position=current_teams[team_id].actual_position,
                    previous_penalty=old.penalty,
                    current_penalty=current_teams[team_id].penalty,
                    penalty_change=current_teams[team_id].penalty - old.penalty,
                )
                for team_id, old in previous_teams.items()
                if current_teams[team_id].penalty != old.penalty
            ),
            key=lambda item: (-abs(item.penalty_change), item.team_name.casefold()),
        )
    )
    return PlayerImpact(
        player_id=previous.player.id,
        display_name=previous.player.display_name,
        previous_rank=previous.score.rank,
        current_rank=current.score.rank,
        rank_change=previous.score.rank - current.score.rank,
        previous_score=previous.score.total,
        current_score=current.score.total,
        score_change=current.score.total - previous.score.total,
        team_changes=team_changes,
    )


def result_matches_transition(
    result: FootballResult,
    before: StandingsSnapshot,
    after: StandingsSnapshot,
) -> bool:
    old = {row.team_id: row for row in before.rows}
    new = {row.team_id: row for row in after.rows}
    if old.keys() != new.keys():
        return False
    changed_played: set[int] = set()
    for team_id, old_row in old.items():
        new_row = new[team_id]
        if old_row.played is None or new_row.played is None:
            return False
        if new_row.played != old_row.played:
            changed_played.add(team_id)
    expected = {result.home_team_id, result.away_team_id}
    if changed_played != expected:
        return False
    home_old, home_new = old[result.home_team_id], new[result.home_team_id]
    away_old, away_new = old[result.away_team_id], new[result.away_team_id]
    rows = (home_old, home_new, away_old, away_new)
    if any(
        row.points is None or row.goal_difference is None or row.goals_scored is None
        for row in rows
    ):
        return False
    assert home_old.points is not None and home_new.points is not None
    assert away_old.points is not None and away_new.points is not None
    assert home_old.goal_difference is not None and home_new.goal_difference is not None
    assert away_old.goal_difference is not None and away_new.goal_difference is not None
    assert home_old.goals_scored is not None and home_new.goals_scored is not None
    assert away_old.goals_scored is not None and away_new.goals_scored is not None
    assert home_old.played is not None and home_new.played is not None
    assert away_old.played is not None and away_new.played is not None
    if result.home_score > result.away_score:
        point_deltas = (3, 0)
    elif result.home_score < result.away_score:
        point_deltas = (0, 3)
    else:
        point_deltas = (1, 1)
    goal_difference = result.home_score - result.away_score
    return (
        home_new.played == home_old.played + 1
        and away_new.played == away_old.played + 1
        and home_new.points - home_old.points == point_deltas[0]
        and away_new.points - away_old.points == point_deltas[1]
        and home_new.goal_difference - home_old.goal_difference == goal_difference
        and away_new.goal_difference - away_old.goal_difference == -goal_difference
        and home_new.goals_scored - home_old.goals_scored == result.home_score
        and away_new.goals_scored - away_old.goals_scored == result.away_score
    )
