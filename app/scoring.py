from dataclasses import dataclass


@dataclass(frozen=True)
class TeamScore:
    team_id: int
    predicted_position: int
    actual_position: int
    penalty: int

    @property
    def exact(self) -> bool:
        return self.penalty == 0


@dataclass(frozen=True)
class PlayerScore:
    player_id: int
    breakdown: tuple[TeamScore, ...]
    total: int
    exact_count: int
    largest_error: int
    rank: int = 0


def score_prediction(
    player_id: int,
    prediction: dict[int, int],
    standings: dict[int, int],
) -> PlayerScore:
    if prediction.keys() != standings.keys() or not prediction:
        raise ValueError("Prediction and standings must contain the same teams.")
    breakdown = tuple(
        TeamScore(
            team_id=team_id,
            predicted_position=predicted_position,
            actual_position=standings[team_id],
            penalty=abs(predicted_position - standings[team_id]),
        )
        for team_id, predicted_position in sorted(prediction.items(), key=lambda item: item[1])
    )
    return PlayerScore(
        player_id=player_id,
        breakdown=breakdown,
        total=sum(item.penalty for item in breakdown),
        exact_count=sum(item.exact for item in breakdown),
        largest_error=max(item.penalty for item in breakdown),
    )


def rank_scores(scores: list[PlayerScore]) -> list[PlayerScore]:
    ordered = sorted(scores, key=lambda item: (item.total, -item.exact_count, item.largest_error))
    ranked: list[PlayerScore] = []
    previous_key: tuple[int, int, int] | None = None
    previous_rank = 0
    for index, item in enumerate(ordered, start=1):
        key = (item.total, item.exact_count, item.largest_error)
        rank = previous_rank if key == previous_key else index
        ranked.append(
            PlayerScore(
                player_id=item.player_id,
                breakdown=item.breakdown,
                total=item.total,
                exact_count=item.exact_count,
                largest_error=item.largest_error,
                rank=rank,
            )
        )
        previous_key = key
        previous_rank = rank
    return ranked
