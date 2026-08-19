from app.media_predictions import MEDIA_PREDICTIONS
from app.teams.service import FIXED_2026_27_TEAMS


def test_media_predictions_contain_each_season_team_once() -> None:
    expected = {team.name for team in FIXED_2026_27_TEAMS}
    for prediction in MEDIA_PREDICTIONS:
        assert len(prediction.teams) == 20
        assert set(prediction.teams) == expected
        if prediction.expected_points:
            assert len(prediction.expected_points) == 20
            assert list(map(float, prediction.expected_points)) == sorted(
                map(float, prediction.expected_points), reverse=True
            )
