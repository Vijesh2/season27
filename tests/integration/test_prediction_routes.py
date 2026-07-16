import re

from starlette.testclient import TestClient

from app.auth.service import development_player_seeds
from app.config import Settings
from app.main import create_app


def login(client: TestClient, code: str) -> None:
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)', page.text)
    assert csrf
    response = client.post(
        "/login", data={"code": code, "csrf_token": csrf.group(1)}, follow_redirects=False
    )
    assert response.status_code == 303


def editable_client(database_url: str) -> TestClient:
    settings = Settings(database_url=database_url, dev_now="2026-08-02T12:00:00")
    return TestClient(create_app(settings))


def prediction_values(page: str) -> tuple[str, list[str]]:
    csrf = re.search(r'name="csrf_token" value="([^"]+)', page)
    assert csrf
    return csrf.group(1), re.findall(r'name="team_id" value="([0-9]+)', page)


def test_prediction_page_is_private_and_read_only_before_open(client: TestClient) -> None:
    assert client.get("/prediction", follow_redirects=False).status_code == 303
    login(client, development_player_seeds()[1].code)
    page = client.get("/prediction")
    assert page.status_code == 200
    assert "Predictions are read-only" in page.text
    assert page.text.count('class="prediction-row"') == 20


def test_player_can_move_save_and_restore_draft(database_url: str) -> None:
    with editable_client(database_url) as client:
        login(client, development_player_seeds()[1].code)
        initial = client.get("/prediction")
        csrf, ids = prediction_values(initial.text)
        moved = client.post(
            f"/prediction/move/{ids[0]}/down",
            data={"csrf_token": csrf},
            follow_redirects=False,
        )
        assert moved.status_code == 303
        _, moved_ids = prediction_values(client.get("/prediction").text)
        assert moved_ids[:2] == [ids[1], ids[0]]
        reversed_ids = list(reversed(moved_ids))
        saved = client.post(
            "/prediction",
            data={"csrf_token": csrf, "team_id": reversed_ids},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        client.cookies.clear()
        login(client, development_player_seeds()[1].code)
        assert prediction_values(client.get("/prediction").text)[1] == reversed_ids


def test_players_have_independent_drafts(database_url: str) -> None:
    with editable_client(database_url) as first, editable_client(database_url) as second:
        seeds = development_player_seeds()
        login(first, seeds[1].code)
        csrf, ids = prediction_values(first.get("/prediction").text)
        first.post(
            f"/prediction/move/{ids[0]}/down",
            data={"csrf_token": csrf},
        )
        login(second, seeds[2].code)
        second_ids = prediction_values(second.get("/prediction").text)[1]
        assert second_ids[:2] == ids[:2]


def test_save_rejects_csrf_and_malformed_order(database_url: str) -> None:
    with editable_client(database_url) as client:
        login(client, development_player_seeds()[1].code)
        csrf, ids = prediction_values(client.get("/prediction").text)
        assert client.post("/prediction", data={"csrf_token": "bad"}).status_code == 403
        response = client.post(
            "/prediction",
            data={"csrf_token": csrf, "team_id": ids[:-1]},
        )
        assert response.status_code == 422


def test_player_reviews_submits_and_resubmits(database_url: str) -> None:
    with editable_client(database_url) as client:
        login(client, development_player_seeds()[1].code)
        review = client.get("/prediction/review")
        assert review.status_code == 200
        assert "Review prediction" in review.text
        csrf = re.search(r'name="csrf_token" value="([^"]+)', review.text)
        assert csrf
        assert (
            client.post(
                "/prediction/submit",
                data={"csrf_token": csrf.group(1)},
            ).status_code
            == 422
        )
        submitted = client.post(
            "/prediction/submit",
            data={"csrf_token": csrf.group(1), "confirmed": "yes"},
            follow_redirects=False,
        )
        assert submitted.status_code == 303
        page = client.get("/prediction")
        assert "Submitted:" in page.text
        csrf_value, ids = prediction_values(page.text)
        client.post(
            f"/prediction/move/{ids[0]}/down",
            data={"csrf_token": csrf_value},
        )
        assert "changes that have not been submitted" in client.get("/prediction").text
        review = client.get("/prediction/review")
        csrf = re.search(r'name="csrf_token" value="([^"]+)', review.text)
        assert csrf
        assert (
            client.post(
                "/prediction/submit",
                data={"csrf_token": csrf.group(1), "confirmed": "yes"},
            ).status_code
            == 200
        )


def test_deadline_request_locks_submitted_player_and_excludes_other(database_url: str) -> None:
    settings = Settings(database_url=database_url, dev_now="2026-08-20T23:59:59")
    app = create_app(settings)
    with TestClient(app) as client:
        seeds = development_player_seeds()
        login(client, seeds[1].code)
        review = client.get("/prediction/review")
        csrf = re.search(r'name="csrf_token" value="([^"]+)', review.text)
        assert csrf
        client.post(
            "/prediction/submit",
            data={"csrf_token": csrf.group(1), "confirmed": "yes"},
        )
    locked_settings = Settings(database_url=database_url, dev_now="2026-08-21T00:00:00")
    with TestClient(create_app(locked_settings)) as submitted_client:
        login(submitted_client, seeds[1].code)
        assert "final locked prediction" in submitted_client.get("/prediction").text
    with TestClient(create_app(locked_settings)) as excluded_client:
        login(excluded_client, seeds[2].code)
        response = excluded_client.get("/prediction")
        assert response.status_code == 403
        assert "No prediction was submitted" in response.text
