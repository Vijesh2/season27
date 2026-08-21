from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, Prediction, PredictionStatus, StandingsSnapshot
from app.scoring import PlayerScore, rank_scores, score_prediction


@dataclass(frozen=True)
class LeaderboardEntry:
    player: Player
    score: PlayerScore


def _positions_for_scoring(snapshot: StandingsSnapshot) -> dict[int, int]:
    first_position_by_record: dict[tuple[int, int, int], int] = {}
    positions: dict[int, int] = {}
    for row in snapshot.rows:
        if (
            row.points is None
            or row.goal_difference is None
            or row.goals_scored is None
        ):
            positions[row.team_id] = row.position
            continue
        record = (row.points, row.goal_difference, row.goals_scored)
        positions[row.team_id] = first_position_by_record.setdefault(record, row.position)
    return positions


def build_leaderboard(
    session: Session, season_id: int, snapshot: StandingsSnapshot
) -> list[LeaderboardEntry]:
    # Publishers use a unique (often alphabetical) order when sporting records
    # are equal. Collapse those arbitrary distinctions into competition ranks.
    actual = _positions_for_scoring(snapshot)
    eligible = session.execute(
        select(Player, PredictionStatus)
        .join(PredictionStatus, PredictionStatus.player_id == Player.id)
        .where(
            PredictionStatus.season_id == season_id,
            PredictionStatus.locked_at.is_not(None),
            PredictionStatus.excluded_at.is_(None),
            Player.is_active.is_(True),
        )
    ).all()
    players = {player.id: player for player, _status in eligible}
    scores: list[PlayerScore] = []
    for player_id in players:
        predictions = session.scalars(
            select(Prediction).where(
                Prediction.player_id == player_id,
                Prediction.season_id == season_id,
            )
        ).all()
        predicted = {item.team_id: item.predicted_position for item in predictions}
        scores.append(score_prediction(player_id, predicted, actual))
    return [
        LeaderboardEntry(player=players[item.player_id], score=item)
        for item in rank_scores(scores)
    ]


def find_entry(entries: list[LeaderboardEntry], player_id: int) -> LeaderboardEntry | None:
    return next((entry for entry in entries if entry.player.id == player_id), None)
