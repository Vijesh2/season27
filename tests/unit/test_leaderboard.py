from types import SimpleNamespace

from app.leaderboard.service import _positions_for_scoring


def standing(
    team_id: int,
    position: int,
    *,
    played: int,
    points: int,
    goal_difference: int,
    goals_scored: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        team_id=team_id,
        position=position,
        played=played,
        points=points,
        goal_difference=goal_difference,
        goals_scored=goals_scored,
    )


def test_positions_use_points_goal_difference_then_goals_scored() -> None:
    snapshot = SimpleNamespace(
        rows=[
            standing(1, 1, played=2, points=3, goal_difference=1, goals_scored=3),
            standing(2, 2, played=1, points=3, goal_difference=1, goals_scored=2),
            standing(3, 3, played=1, points=3, goal_difference=0, goals_scored=5),
            standing(4, 4, played=1, points=1, goal_difference=5, goals_scored=8),
        ]
    )

    assert _positions_for_scoring(snapshot) == {1: 1, 2: 2, 3: 3, 4: 4}


def test_games_played_do_not_separate_otherwise_identical_records() -> None:
    snapshot = SimpleNamespace(
        rows=[
            standing(1, 1, played=2, points=3, goal_difference=1, goals_scored=2),
            standing(2, 2, played=1, points=3, goal_difference=1, goals_scored=2),
        ]
    )

    assert _positions_for_scoring(snapshot) == {1: 1, 2: 1}
