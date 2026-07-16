from app.scoring import PlayerScore, TeamScore, rank_scores, score_prediction


def test_score_is_sum_of_absolute_position_errors() -> None:
    prediction = {1: 1, 2: 2, 3: 3, 4: 4}
    standings = {1: 3, 2: 2, 3: 1, 4: 4}
    score = score_prediction(7, prediction, standings)
    assert [item.penalty for item in score.breakdown] == [2, 0, 2, 0]
    assert score.total == 4
    assert score.exact_count == 2
    assert score.largest_error == 2


def player_score(
    player_id: int, total: int, exact_count: int, largest_error: int
) -> PlayerScore:
    return PlayerScore(
        player_id=player_id,
        breakdown=(TeamScore(player_id, 1, 1, 0),),
        total=total,
        exact_count=exact_count,
        largest_error=largest_error,
    )


def test_rank_uses_score_exact_count_and_smallest_worst_error() -> None:
    ranked = rank_scores(
        [
            player_score(1, 12, 3, 4),
            player_score(2, 10, 1, 5),
            player_score(3, 10, 2, 6),
            player_score(4, 10, 2, 3),
        ]
    )
    assert [item.player_id for item in ranked] == [4, 3, 2, 1]
    assert [item.rank for item in ranked] == [1, 2, 3, 4]


def test_complete_ties_share_rank_and_next_rank_is_skipped() -> None:
    ranked = rank_scores(
        [
            player_score(1, 8, 4, 2),
            player_score(2, 8, 4, 2),
            player_score(3, 10, 5, 1),
        ]
    )
    assert [item.rank for item in ranked] == [1, 1, 3]


def test_score_requires_matching_nonempty_teams() -> None:
    for prediction, standings in (({}, {}), ({1: 1}, {2: 1})):
        try:
            score_prediction(1, prediction, standings)
        except ValueError as error:
            assert "same teams" in str(error)
        else:
            raise AssertionError("Invalid score inputs were accepted")
