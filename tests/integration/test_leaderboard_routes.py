import re
from datetime import datetime

from sqlalchemy import delete
from starlette.testclient import TestClient

from app.auth.service import development_player_seeds
from app.clock import LONDON
from app.config import Settings
from app.db.models import Standing, StandingsSnapshot
from app.main import create_app
from app.standings.service import StandingInput, create_snapshot, get_latest_snapshot


def login(client: TestClient, code: str) -> None:
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)', page.text)
    assert csrf
    response = client.post(
        "/login",
        data={"code": code, "csrf_token": csrf.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def submit_players(database_url: str, indexes: tuple[int, ...]) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-20T12:00:00")
    seeds = development_player_seeds()
    for index in indexes:
        with TestClient(create_app(settings)) as client:
            login(client, seeds[index].code)
            review = client.get("/prediction/review")
            csrf = re.search(r'name="csrf_token" value="([^"]+)', review.text)
            assert csrf
            response = client.post(
                "/prediction/submit",
                data={"csrf_token": csrf.group(1), "confirmed": "yes"},
                follow_redirects=False,
            )
            assert response.status_code == 303


def results_client(database_url: str, player_index: int) -> TestClient:
    settings = Settings(database_url=database_url, dev_now="2026-09-01T12:00:00")
    client = TestClient(create_app(settings))
    login(client, development_player_seeds()[player_index].code)
    return client


def test_predictions_stay_private_before_deadline_even_for_admin(database_url: str) -> None:
    submit_players(database_url, (0, 1))
    settings = Settings(database_url=database_url, dev_now="2026-08-20T23:59:59")
    for player_index in (0, 1):
        with TestClient(create_app(settings)) as client:
            login(client, development_player_seeds()[player_index].code)
            assert client.get("/leaderboard").status_code == 403
            assert client.get("/leaderboard/1").status_code == 403
            assert client.get("/activity").status_code == 403


def test_leaderboard_reveals_scores_and_player_details_after_deadline(
    database_url: str,
) -> None:
    submit_players(database_url, (0, 1))
    with results_client(database_url, 1) as client:
        page = client.get("/leaderboard")
        assert page.status_code == 200
        assert "As it stands" in page.text
        assert "Standings recorded" in page.text
        assert development_player_seeds()[0].display_name in page.text
        assert development_player_seeds()[1].display_name in page.text
        assert development_player_seeds()[2].display_name not in page.text
        assert page.text.count("/leaderboard/") == 2
        detail = client.get("/leaderboard/1")
        assert detail.status_code == 200
        assert development_player_seeds()[0].display_name in detail.text
        assert "prediction" in detail.text
        assert "Predicted" in detail.text
        assert "Actual" in detail.text
        assert "Penalty" in detail.text
        assert detail.text.count("<tr>") == 21
        assert client.get("/leaderboard/99999").status_code == 404


def test_final_snapshot_is_labelled_final(database_url: str) -> None:
    submit_players(database_url, (1,))
    app = create_app(Settings(database_url=database_url, dev_now="2027-05-01T12:00:00"))
    with app.state.session_factory() as session:
        current = get_latest_snapshot(session, 1)
        assert current is not None
        create_snapshot(
            session,
            1,
            [StandingInput(team_id=row.team_id, position=row.position) for row in current.rows],
            datetime(2027, 5, 1, 12, tzinfo=LONDON),
            source="test",
            is_final=True,
        )
    with TestClient(app) as client:
        login(client, development_player_seeds()[1].code)
        page = client.get("/leaderboard")
        assert '<p class="result-state">Final</p>' in page.text


def test_results_require_authentication_and_excluded_players_lose_access(
    database_url: str,
) -> None:
    submit_players(database_url, (1,))
    settings = Settings(database_url=database_url, dev_now="2026-09-01T12:00:00")
    with TestClient(create_app(settings)) as anonymous:
        assert anonymous.get("/leaderboard", follow_redirects=False).status_code == 303
    with results_client(database_url, 2) as excluded:
        assert excluded.get("/leaderboard").status_code == 403
        assert excluded.get("/activity").status_code == 403


def test_empty_standings_state_is_clear(database_url: str) -> None:
    submit_players(database_url, (1,))
    app = create_app(Settings(database_url=database_url, dev_now="2026-09-01T12:00:00"))
    with app.state.session_factory() as session:
        session.execute(delete(Standing))
        session.execute(delete(StandingsSnapshot))
        session.commit()
    with TestClient(app) as client:
        login(client, development_player_seeds()[1].code)
        page = client.get("/leaderboard")
        assert page.status_code == 200
        assert "Standings are not available yet" in page.text


def test_shared_activity_shows_other_players_swaps(database_url: str) -> None:
    submit_players(database_url, (1, 2))
    with results_client(database_url, 1) as first:
        swaps = first.get("/swaps")
        csrf = re.search(r'name="csrf_token" value="([^"]+)', swaps.text)
        team_ids = re.findall(r'type="checkbox" name="team_id" value="([0-9]+)', swaps.text)
        assert csrf and len(team_ids) == 20
        first.post(
            "/swaps/confirm",
            data={
                "csrf_token": csrf.group(1),
                "first_team_id": team_ids[0],
                "second_team_id": team_ids[-1],
                "confirmed": "yes",
            },
        )
    with results_client(database_url, 2) as second:
        activity = second.get("/activity")
        assert activity.status_code == 200
        assert development_player_seeds()[1].display_name in activity.text
        assert "in window 1" in activity.text
